from concurrent.futures import process
import logging
import base64
from urllib import response
import click
import csv
import os
import re
import sys
from datetime import datetime
from datetime import UTC
from datetime import timedelta
from dateutil.parser import parse
import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3 import Retry
import subprocess
import yaml
import json


default_namespace = 'giantswarm'
default_workdir = '.'
tags_path = 'tags.csv'
sync_conf_path = 'sync.yaml'

source_username = os.getenv('SOURCE_USERNAME')
source_password = os.getenv('SOURCE_PASSWORD')
target_username = os.getenv('TARGET_USERNAME')
target_password = os.getenv('TARGET_PASSWORD')

scope_catalog = 'registry:catalog:*'
scope_repository = 'repository:*:pull'
scope_metadata = 'repository:*:metadata_read'

# Global dict that will hold our access tokens.
ACCESS_TOKENS = {
    scope_catalog: '',
    scope_repository: '',
    scope_metadata: '',
}

# Configure HTTP retries
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=0.1,
    status_forcelist=[500, 502, 503, 504]
)
session.mount('https://', HTTPAdapter(max_retries=retries))


@click.group()
def cli():
    global logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def get_acr_access_token(acr_name, scope):
    """
    Obtains a new ACR access token, given an
    [ACR Token](https://learn.microsoft.com/en-gb/azure/container-registry/container-registry-token-based-repository-permissions)
    username and password.
    """
    global source_username, source_password
    params = {
        'service': f'{acr_name}.azurecr.io',
        'scope': scope,
    }
    response = session.get(f'https://{acr_name}.azurecr.io/oauth2/token', params=params, auth=HTTPBasicAuth(source_username, source_password))
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        logger.error(f"Error fetching AAD access token: {e}")
        return None
    token = response.json()['access_token']
    logger.debug(f"Fetched access token with scope '{scope}': {token}")
    return token


def get_jwt_expiry(token):
    """
    Returns the JWT's exp value as datetime
    """
    _, payload, _ = token.split('.')
    payload = json.loads(base64.urlsafe_b64decode(payload + '=='))
    exp = payload.get('exp')
    if exp is None:
        raise ValueError("JWT token does not contain 'exp' claim.")
    return datetime.fromtimestamp(exp, tz=UTC)


def ensure_fresh_access_token(acr_name, scope):
    """
    Checks if ACCESS_TOKEN is still valid.
    If not, it updates it.
    """
    global ACCESS_TOKENS
    if ACCESS_TOKENS[scope] == '':
        ACCESS_TOKENS[scope] = get_acr_access_token(acr_name, scope)
        return
    expiry = get_jwt_expiry(ACCESS_TOKENS[scope])
    if expiry < datetime.now(UTC) + timedelta(minutes=1):
        logger.debug("Access token is expired or about to expire. Fetching a new one.")
        ACCESS_TOKENS[scope] = get_acr_access_token(acr_name, scope)


def get_acr_repositories(acr_name):
    """
    Retrieves a list of all repositories in the specified ACR
    as an iterator.
    """
    global ACCESS_TOKENS, scope_catalog
    params = {'n': 100, 'orderby': ''}
    has_more = True
    repositories = set()

    while has_more:
        num_repositories = len(repositories)
        ensure_fresh_access_token(acr_name, scope_catalog)
        headers = {'Authorization': f"Bearer {ACCESS_TOKENS[scope_catalog]}"}
        response = session.get(f'https://{acr_name}.azurecr.io/v2/_catalog', headers=headers, params=params)
        response.raise_for_status()
        
        payload = response.json()
        try:
            for repo in payload['repositories']:
                repositories.add(repo)
                yield repo
                params['last'] = repo
        except:
            pass

        if num_repositories == len(repositories):
            has_more = False


def get_acr_tags(acr_name, repository):
    """
    Returns a list of image tags for the given image repository.
    """
    global ACCESS_TOKENS, scope_metadata
    params = {'n': 100, 'orderby': ''}
    has_more = True
    alltags = set()

    while has_more:
        ensure_fresh_access_token(acr_name, scope_metadata)
        headers = {'Authorization': f"Bearer {ACCESS_TOKENS[scope_metadata]}"}
        response = session.get(f'https://{acr_name}.azurecr.io/acr/v1/{repository}/_tags', headers=headers, params=params)

        try:
            response.raise_for_status()
        except:
            print(response.request.url)
            print(response.request.headers)
            print(response.headers)
            print(response.text)
            sys.exit(1)
        payload = response.json()
        tags = payload['tags']
        
        for tag in tags:
            yield tag
            alltags.add(tag['name'])
            params['last'] = tag['name']

        if not response.links.get('next'):
            has_more = False

    logger.debug(f"Found {len(alltags)} tags in repository '{repository}'")


@click.command()
@click.option('--registry-name', help='Container registry name, either "gsoci" or "gsociprivate"')
@click.option('--namespace', default=default_namespace, help='Repository namespace to crawl.')
@click.option('--repository', default='', help='Repository name to crawl. Leave empty to crawl all repositories (default). Specify without namespace prefix.')
@click.option('--repository-regex', default='', help='Repository name regex. If specified, only matching repositories will be crawled. Does not cover namespace prefix.')
@click.option('--skip-private', default=True, help='Skip crawling private repositories.')
@click.option('--tag-regex', default='', help='Tag name regex. If specified, only matching tags will be returned.')
@click.option('--tag-min-age', default='', help='Only collect tags with an age greater than the specified number of days.')
@click.option('--tag-max-age', default='', help='Only collect tags with an age smaller than the specified number of days.')
@click.option('--workdir', default=default_workdir, help='Working directory for CSV files.')
def crawl(registry_name, namespace, repository, repository_regex, skip_private, tag_regex, tag_min_age, tag_max_age, workdir):
    """
    Collect information on repositories and tags in the source registryin CSV files.
    """
    global source_username, source_password

    if source_username is None or source_username == '':
        logger.error('SOURCE_USERNAME environment variable must be set.')
        sys.exit(1)
    if source_password is None or source_password == '':
        logger.error('SOURCE_PASSWORD environment variable must be set.')
        sys.exit(1)

    if repository != '' and repository_regex != '':
        logger.error('Only one of --repository or --repository-regex can be set.')
        sys.exit(1)

    if registry_name not in ('gsoci', 'gsociprivate'):
        logger.error('Invalid registry name. Please use either "gsoci" or "gsociprivate".')
        sys.exit(1)

    repositories = []

    logger.info("Collecting repositories...")

    with open(workdir+os.path.sep+'repositories.csv', 'w') as csvfile:
        writer = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)
        fieldnames = ['namespace', 'name']
        writer.writerow(fieldnames)

        for r in get_acr_repositories(registry_name):
            my_namespace = ''
            if "/" in r:
                my_namespace, repo_name = r.split('/', maxsplit=1)

            # Apply filter
            if namespace != '' and my_namespace != namespace:
                continue
            if repository != '' and repo_name != repository:
                continue
            elif repository_regex != '' and not re.match(repository_regex, repo_name):
                continue

            repositories.append(r)
            writer.writerow([namespace, repo_name])

    logger.info(f'Found {len(repositories)} repositories.')

    with open(workdir+os.path.sep+tags_path, 'w') as csvfile:
        writer = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)
        fieldnames = ['namespace', 'repo_name', 'name', 'created_on', 'last_updated_on', 'digest']
        writer.writerow(fieldnames)

        for r in repositories:
            try:
                my_namespace, repo_name = r.split('/', maxsplit=1)
            except ValueError:
                my_namespace = ''
                repo_name = r
            
            if namespace != '' and my_namespace != namespace:
                logger.debug(f'Namespace {my_namespace} does not match {namespace}')
                continue
            if repository != '' and repo_name != repository:
                continue
            elif repository_regex != '' and not re.match(repository_regex, repo_name):
                continue
            
            logger.info(f'Repository {r}')
            all_tags = []

            try:
                for tag in get_acr_tags(registry_name, r):
                    name = tag.get('name')

                    # Apply filters
                    cutoff_min = datetime.now(UTC)
                    cutoff_max = datetime.now(UTC)
                    tag_updated_time = parse(tag.get('lastUpdateTime'))

                    if tag_min_age != '':
                        cutoff_min = cutoff_min - timedelta(days=int(tag_min_age))
                    if tag_max_age != '':
                        cutoff_max = cutoff_max - timedelta(days=int(tag_max_age))

                    if tag_min_age != '' and tag_updated_time > cutoff_min:
                        logger.debug(f'Tag "{name}" is younger than "{tag_min_age}" day(s)')
                        continue
                    if tag_max_age != '' and tag_updated_time < cutoff_max:
                        logger.debug(f'Tag "{name}" is older than "{tag_max_age}" day(s)')
                        continue
                    if tag_regex != '' and not re.match(tag_regex, name):
                        logger.debug(f'Tag "{name}" does not match "{tag_regex}" regex')
                        continue

                    logger.info(f'Repository {r}: saving tag {name} for syncing')
                    all_tags.append(name)
                    writer.writerow([namespace, repo_name, name, tag.get('createdTime'), tag.get('lastUpdateTime'), tag.get('digest')])
            except Exception as e:
                logger.error(f'Error processing tags for repository "{r}": {e}')

            logger.info(f'Repository {r}: {len(all_tags)} tags')


@click.command()
@click.option('--source-registry-name', help='Source container registry name, either "gsoci" or "gsociprivate"')
@click.option('--workdir', default=default_workdir, help=f'Working directory for CSV and YAML files. (default: {default_workdir})')
@click.option('--target-registry', default='docker.io', help='Target registry to sync to (default: docker.io).')
@click.option('--target-namespace', default='giantswarm', help='Target repository namespace prefix (default: giantswarm).')
def sync(source_registry_name, workdir, target_registry, target_namespace):
    """
    Synchronize tags defined in tags.csv to the destination registry.
    """

    if source_username is None or source_username == '':
        logger.error('SOURCE_USERNAME environment variable must be set.')
        sys.exit(1)
    if source_password is None or source_password == '':
        logger.error('SOURCE_PASSWORD environment variable must be set.')
        sys.exit(1)
    if target_username is None or target_username == '':
        logger.error('TARGET_USERNAME environment variable must be set.')
        sys.exit(1)
    if target_password is None or target_password == '':
        logger.error('TARGET_PASSWORD environment variable must be set.')
        sys.exit(1)
    if source_registry_name not in ('gsoci', 'gsociprivate'):
        logger.error('Invalid source registry name. Please use either "gsoci" or "gsociprivate".')
        sys.exit(1)

    # Create skopeo sync YAML file
    reg_key = f'{source_registry_name}.azurecr.io'
    conf = {
        reg_key: {
            'images': {},
        }
    }

    # Populate config file from tags CSV file
    with open(workdir+os.path.sep+tags_path, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            repokey = row['namespace'] + '/' + row['repo_name']
            if repokey not in conf[reg_key]['images']:
                conf[reg_key]['images'][repokey] = []
            conf[reg_key]['images'][repokey].append(row['name'])

    # Write sync config to file
    with open(workdir+os.path.sep+sync_conf_path, 'w') as f:
        yaml.dump(conf, f)

    # Execute skopeo sync with config
    command = [
        'skopeo',
        'sync',
        '--retry-times', '3',
        '--keep-going',
        '--src', 'yaml',
        '--src-creds', f'{source_username}:{source_password}',
        '--dest', 'docker',
        '--dest-creds', f'{target_username}:{target_password}',
        workdir+os.path.sep+sync_conf_path,
        f'{target_registry}/{target_namespace}',
    ]
    subprocess.run(command)


cli.add_command(crawl)
cli.add_command(sync)


if __name__ == '__main__':
    try:
        cli()
    except BaseException as error:
        logger.error(f'An exception occurred: {error}')
