[![CircleCI](https://dl.circleci.com/status-badge/img/gh/giantswarm/registry-sync/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/giantswarm/registry-sync/tree/main)

# registry-sync

A synchronization utility to keep certain images in sync between multiple container registries. It assumes an Azure Container Registry (ACR) as source.

The main application is a Python CLI script that is configured to read repositories and tags from a source registry, then creates CSV files listing the images and tags to replicate, and then applies `skopeo sync` to do the actual synchronization.

## Docker container

The docker image is available as `gsoci.azurecr.io/giantswarm/registry-sync` with tags according to the release versions, without `v` prefix.

## Usage

The CLI provides two commands, `crawl` and `sync`. To run the `sync` command, you need the `tags.csv` file produced by `crawl`.

### crawl

The `crawl` command collects information on repositories and tags in the source registry and writes them to CSV files.

For this command, the Azure CLI must be used before execution to authenticate against the source registry (`az acr login`).

Example usage:

```nohighlight
python main.py crawl --registry-name gsoci --namespace giantswarm
```

Note that the `--registry-name` must be specified without the `.azurecr.io` suffix.

As a result, the file `repositories.csv` and `tags.csv` will be created in the working directory. The working directory can be specified via the `--workdir` flag.

For more options, see `python main.py crawl --help`.

### sync

The `sync` command reads `tags.csv` produced by the `crawl` command and applies `skopeo sync` to synchronize the images to the target registry.

For authentication to both the source and the target registries, the following environment variables must be set:

- `SOURCE_USERNAME`
- `SOURCE_PASSWORD`
- `TARGET_USERNAME`
- `TARGET_PASSWORD`

```nohighlight
python main.py sync \
    --source-registry-name gsoci \
    --namespace giantswarm \
    --target-registry-name docker.io \
    --target-namespace giantswarm

```

## Deployment

To be deployed as a Kubernetes CronJob. Example:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: registry-sync-gsoci-to-dockerio
spec:
  schedule: "48 3 * * *" # daily at 03:48
  jobTemplate:
    spec:
      template:
        spec:
          initContainers:
            - name: crawl
              image: gsoci.azurecr.io/giantswarm/registry-sync:latest
              imagePullPolicy: IfNotPresent
              command:
                - crawl
                - --registry-name=gsoci
                - --namespace=giantswarm
                - --workdir=/data
              env:
                - name: SOURCE_USERNAME
                  valueFrom:
                    secretKeyRef:
                      name: mysecret
                      key: gsoci-username
                - name: SOURCE_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: mysecret
                      key: gsoci-password
              volumeMounts:
                - name: data
                  mountPath: /data
            - name: sync
              image: gsoci.azurecr.io/giantswarm/registry-sync:latest
              imagePullPolicy: IfNotPresent
              command:
                - sync
                - --source-registry-name=gsoci
                - --namespace=giantswarm
                - --target-registry-name=docker.io
                - --target-namespace=giantswarm
                - --workdir=/data
              env:
                - name: SOURCE_USERNAME
                  valueFrom:
                    secretKeyRef:
                      name: mysecret
                      key: gsoci-username
                - name: SOURCE_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: mysecret
                      key: gsoci-password
                - name: TARGET_USERNAME
                  valueFrom:
                    secretKeyRef:
                      name: mysecret
                      key: docker-username
                - name: TARGET_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: mysecret
                      key: docker-password
              volumeMounts:
                - name: data
                  mountPath: /data
          containers:
            - name: job-done
              image: busybox
              command: ['sh', '-c', 'echo "completed"']
              imagePullPolicy: IfNotPresent
          restartPolicy: Never
          volumes:
            - name: data
              emptyDir: {}
```

## Development

See `docs/`.
