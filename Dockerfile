FROM gsoci.azurecr.io/giantswarm/python:3.13.5-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends skopeo curl azure-cli jq && apt-get clean

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install --break-system-packages -r requirements.txt

COPY main.py /app/main.py

ENTRYPOINT ["python", "/app/main.py"]

