#!/usr/bin/env bash
# Deploy MatchMind with the Azure CLI only (no azd, no local Docker).
#   1. provision infra/main.bicep (Foundry + model, Container Apps, ACR, Cosmos DB, Translator, App Insights)
#   2. build the image remotely in Azure Container Registry
#   3. roll the Container App onto that image
#
# Usage: infra/deploy.sh [resource-group] [location] [env-name]
set -euo pipefail

AZ="${AZ:-az}"
RG="${1:-rg-matchmind}"
LOCATION="${2:-eastus2}"
ENV_NAME="${3:-matchmind}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

echo "==> resource group $RG ($LOCATION)"
"$AZ" group create -n "$RG" -l "$LOCATION" -o none

echo "==> provisioning infrastructure (first run takes ~5-8 min)"
OUT=$("$AZ" deployment group create -g "$RG" -n matchmind-infra -f "$HERE/main.bicep" \
  -p environmentName="$ENV_NAME" location="$LOCATION" \
     fabricEventstreamConnectionString="${FABRIC_EVENTSTREAM_CONNECTION_STRING:-}" \
  --query properties.outputs -o json)
out() { python3 -c 'import json,sys; d={k.lower(): v["value"] for k, v in json.load(sys.stdin).items()}; print(d[sys.argv[1].lower()])' "$1"; }
ACR=$(echo "$OUT" | out AZURE_CONTAINER_REGISTRY_ENDPOINT)
APP=$(echo "$OUT" | out SERVICE_WEB_NAME)
TAG="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%s)"
IMAGE="$ACR/matchmind:$TAG"

echo "==> building $IMAGE in ACR"
"$AZ" acr build -r "${ACR%%.*}" -t "matchmind:$TAG" "$ROOT" -o none

echo "==> deploying $IMAGE to $APP"
OUT=$("$AZ" deployment group create -g "$RG" -n matchmind-app -f "$HERE/main.bicep" \
  -p environmentName="$ENV_NAME" location="$LOCATION" webImageName="$IMAGE" \
     fabricEventstreamConnectionString="${FABRIC_EVENTSTREAM_CONNECTION_STRING:-}" \
  --query properties.outputs -o json)
URL=$(echo "$OUT" | out SERVICE_WEB_URI)

echo
echo "MatchMind is live: $URL"
echo "Health:            $URL/api/health"
echo "MCP endpoint:      $URL/mcp/"
