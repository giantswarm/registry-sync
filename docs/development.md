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

```nohighlight
export user=
export password=
credentials=$(echo -n "$user:$password" | base64 -w 0)
curl -H "Authorization: Bearer $credentials" https://gsoci.azurecr.io/v2/_catalog
