FROM gsoci.azurecr.io/giantswarm/python:3.13.5-alpine

WORKDIR /app

RUN apk add --no-cache skopeo curl

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install --break-system-packages -r requirements.txt

COPY main.py /app/main.py

ENTRYPOINT ["python", "/app/main.py"]
