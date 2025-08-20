from concurrent.futures import process
import logging

import click
import csv
import os
import re
import sys
from datetime import datetime
from datetime import UTC
from datetime import timedelta
import pexpect
import yaml
import json
from subprocess import Popen, PIPE


from azure.identity import DefaultAzureCredential
from azure.containerregistry import ContainerRegistryClient

default_namespace = 'giantswarm'
default_workdir = '.'
tags_path = 'tags.csv'
sync_conf_path = 'sync.yaml'

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


@click.group()
def cli():
    pass


def az_acr_login(registry_name, username, password):
    """
    This performs an interactive `az acr login` command with the --expose-token
    flag. We choose this method as it doesn't rely on docker being available.

    Returns username and accessToken in case of success.
    """
    command = f"az acr login --name={registry_name} --expose-token"
    az_acr_login = pexpect.spawn(command)
    i = az_acr_login.expect([pexpect.TIMEOUT, '[Uu]sername: ', '[Pp]assword: ', pexpect.EOF])
    if i == 0: # timeout
        logger.error("az acr login timed out")
        sys.exit(1)
    elif i == 1: # username
        az_acr_login.sendline(username)
    elif i == 2: # password
        az_acr_login.sendline(password)
    elif i == 3: # token
        output = az_acr_login.before.decode('utf-8')
        # get JSON part (snippet between { and })
        json_part = re.search(r'\{.*\}', output, re.DOTALL)
        if json_part:
            json_output = json_part.group(0)
            data = json.loads(json_output)
            logger.info(f"Login successful. Token: {data['accessToken']}")
            return data['username'], data['accessToken']
        else:
            logger.error("Failed to parse JSON from az acr login output")
            sys.exit(1)

    else:
        logger.error("Unexpected output from az acr login")
        sys.exit(1)

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
    source_username = os.getenv('SOURCE_USERNAME')
    source_password = os.getenv('SOURCE_PASSWORD')

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

    username, token = az_acr_login(registry_name, source_username, source_password)

    registry_url = f"https://{registry_name}.azurecr.io"

    logger.info("Setting up ACR client. If this takes more than a few seconds, please quit the process and log in via 'az acr login --name REGISTRY_NAME'")
    credential = DefaultAzureCredential()
    client = ContainerRegistryClient(endpoint=registry_url, credential=credential)

    repositories = []

    logger.info("Collecting repositories...")

    with open(workdir+os.path.sep+'repositories.csv', 'w') as csvfile:
        writer = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)
        fieldnames = ['namespace', 'name']
        writer.writerow(fieldnames)

        for r in client.list_repository_names():
            try:
                my_namespace, repo_name = r.split('/', maxsplit=1)
            except ValueError:
                my_namespace = ''
                repo_name = r

            # Apply filter
            if namespace != '' and my_namespace != namespace:
                continue
            if repository != '' and repo_name != repository:
                continue
            elif repository_regex != '' and not re.match(repository_regex, repo_name):
                continue

            repositories.append(r)
            writer.writerow([namespace, repo_name])

    logger.info(f'Found "{len(repositories)}" repositories.')

    with open(workdir+os.path.sep+tags_path, 'w') as csvfile:
        writer = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)
        fieldnames = ['namespace', 'repo_name', 'name', 'created_on', 'last_updated_on', 'digest']
        writer.writerow(fieldnames)

        for r in client.list_repository_names():
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
                for tag in client.list_tag_properties(r):
                    
                    # Apply filters
                    cutoff_min = datetime.now(UTC)
                    cutoff_max = datetime.now(UTC)

                    if tag_min_age != '':
                        cutoff_min = cutoff_min - timedelta(days=int(tag_min_age))
                    if tag_max_age != '':
                        cutoff_max = cutoff_max - timedelta(days=int(tag_max_age))
                    
                    if tag_min_age != '' and tag.last_updated_on > cutoff_min:
                        logger.debug(f'Tag "{tag.name}" is younger than "{tag_min_age}" day(s)')
                        continue
                    if tag_max_age != '' and tag.last_updated_on < cutoff_max:
                        logger.debug(f'Tag "{tag.name}" is older than "{tag_max_age}" day(s)')
                        continue
                    if tag_regex != '' and not re.match(tag_regex, tag.name):
                        logger.debug(f'Tag "{tag.name}" does not match "{tag_regex}" regex')
                        continue

                    logger.info(f'Repository {r}: saving tag {tag.name} for syncing')
                    all_tags.append(tag.name)
                    writer.writerow([namespace, repo_name, tag.name, tag.created_on, tag.last_updated_on, tag.digest])
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
    source_username = os.getenv('SOURCE_USERNAME')
    source_password = os.getenv('SOURCE_PASSWORD')
    target_username = os.getenv('TARGET_USERNAME')
    target_password = os.getenv('TARGET_PASSWORD')

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
