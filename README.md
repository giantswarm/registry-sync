# registry-sync

A synchronization utility to keep certain images in sync between multiple container registries. It assumes an Azure Container Registry (ACR) as source.

The main application is a Python CLI script that is configured to read repositories and tags from a source registry, then creates CSV files listing the images and tags to replicate, and then applies `skopeo sync` to do the actual synchronization.

## Docker container

The docker image is available as `gsoci.azurecr.io/giantswarm/registry-sync` with tags according to the release versions, without `v` prefix.

## ACR authentication

The script assumes that the Azure CLI has been used to authenticate against the source registry (`az acr login` ).

## Usage

## Deployment

To be deployed as a Kubernetes CronJob.
