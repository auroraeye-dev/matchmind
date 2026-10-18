#!/usr/bin/env bash
# Connect the deployed MatchMind app to a Microsoft Fabric Eventstream.
# The connection string is read without echo, sent only to Azure as a Container App secret,
# and never written to disk or to the repo.
#
# Usage: infra/connect-fabric.sh [resource-group] [location] [env-name]
set -euo pipefail

AZ="${AZ:-az}"
RG="${1:-rg-matchmind}"
LOCATION="${2:-eastus2}"
ENV_NAME="${3:-matchmind}"
HERE="$(cd "$(dirname "$0")" && pwd)"

APP=$("$AZ" resource list -g "$RG" --resource-type Microsoft.App/containerApps --query "[0].name" -o tsv)
IMAGE=$("$AZ" resource show -g "$RG" -n "$APP" --resource-type Microsoft.App/containerApps \
  --query "properties.template.containers[0].image" -o tsv)
echo "Container App: $APP"
echo "Current image: $IMAGE"
echo
echo "Paste the Eventstream custom endpoint connection string"
echo "(Event Hub > SAS key authentication > Connection string-primary key). Input is hidden:"
read -rs CONN
echo
case "$CONN" in
  Endpoint=sb://*SharedAccessKey=*) ;;
  *) echo "That doesn't look like an Event Hubs connection string (Endpoint=sb://...;SharedAccessKey=...)." >&2; exit 1 ;;
esac

echo "==> redeploying with the Fabric sink enabled (keeps the current image)"
"$AZ" deployment group create -g "$RG" -n matchmind-fabric -f "$HERE/main.bicep" \
  -p environmentName="$ENV_NAME" location="$LOCATION" webImageName="$IMAGE" \
     fabricEventstreamConnectionString="$CONN" \
  --query properties.provisioningState -o tsv
unset CONN
echo "Done. Kick off a match on the live site; events appear in the Eventstream within seconds."
