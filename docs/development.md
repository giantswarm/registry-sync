# Developing on registry-sync

Synopsis:

```nohighlight
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

python main.py --help
```

## ACR API

[Reference](https://learn.microsoft.com/en-us/rest/api/containerregistry/?view=rest-containerregistry-2019-08-15)

We use [ACR-internal tokens](https://learn.microsoft.com/en-gb/azure/container-registry/container-registry-token-based-repository-permissions) for authentication.

For the actual API requests, an access token is required, which has to be obtained before via the `/oauth2/token` method.

In the snippets below, the environment variable `SOURCE_USER` represents the token username and `SOURCE_PASSWORD` the token password.

Note that access tokens are short-lived, with TTL around 5 to 20 minutes. So it makes sense to evaluate the token expiry (`exp` claim) before use and fetch a fresh one when needed.

**Watch out:** listing repositories requires an access token with different scope (`registry:catalog:*`) than the other operations (`repository:*:pull`, `repository:*:metadata_read`).

### Listing repositories

Note: this only gets the first 100 repositories. Use `Link` header for pagination.

```nohighlight
# Listing repositories
credentials=$(echo -n "$SOURCE_USER:$SOURCE_PASSWORD" | base64 -w 0)
response=$(curl -sS -H "Authorization: Basic $credentials" "https://gsoci.azurecr.io/oauth2/token?service=gsoci.azurecr.io&scope=registry:catalog:*")
TOKEN=$(echo $response | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" https://gsoci.azurecr.io/v2/_catalog
```

### Getting tags for a repository

Note: this only gets the first 100 tags. Use `Link` header for pagination.

```nohighlight
credentials=$(echo -n "$SOURCE_USER:$SOURCE_PASSWORD" | base64 -w 0)
response=$(curl -sS -H "Authorization: Basic $credentials" "https://gsoci.azurecr.io/oauth2/token?service=gsoci.azurecr.io&scope=repository:*:metadata_read")
TOKEN=$(echo $response | jq -r .access_token)
curl -i -H "Authorization: Bearer $TOKEN" https://gsoci.azurecr.io/acr/v1/giantswarm/alpine/_tags
```
