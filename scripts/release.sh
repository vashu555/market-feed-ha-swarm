#!/usr/bin/env bash
# Release a new version to the Swarm: build ONCE on Mumbai, copy the identical images to
# Hyderabad, check they match, then roll the stack.
#
# Usage (from Git Bash on Windows, or WSL):   scripts/release.sh 1.1
# Needs scripts/.env (copy scripts/env.example).
#
# What users notice: APIs are replaced one at a time (3 keep serving); the generator restarts,
# so prices pause for a few seconds and restart from their starting values.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
source scripts/.env
TAG=${1:?usage: scripts/release.sh <version>   e.g. scripts/release.sh 1.1}
SSH=${SSH_BIN:-ssh}

mumbai()    { "$SSH" -o ConnectTimeout=10 -i "$MUMBAI_KEY" "$MUMBAI_HOST" "$@"; }
hyderabad() { "$SSH" -o ConnectTimeout=10 -i "$HYDERABAD_KEY" "$HYDERABAD_HOST" "$@"; }

# Fingerprint of an image's layers. Not the image ID: Hyderabad's Docker uses the containerd
# image store, which computes IDs differently, so IDs differ even for identical images.
FP='for i in market-api:'"$TAG"' market-generator:'"$TAG"'; do sudo docker image inspect -f "{{range .RootFS.Layers}}{{.}}{{end}}" $i | sha256sum | cut -c1-12; done'

# A version number is used once. Rebuilding an existing tag would change the image under a name
# Swarm thinks is already deployed, so the running containers would silently differ from it.
if mumbai "sudo docker image inspect market-api:$TAG >/dev/null 2>&1"; then
  echo "market-api:$TAG already exists on Mumbai. Use a new version number (e.g. a higher one)." >&2
  exit 1
fi

echo "1/5  Copying source (api, generator, swarm) to ~/market-ha on both VMs"
for vm in mumbai hyderabad; do
  tar --exclude=__pycache__ -czf - api generator swarm | $vm "mkdir -p ~/market-ha && tar -xzf - -C ~/market-ha"
done

echo "2/5  Building market-api:$TAG and market-generator:$TAG on Mumbai (the only place we build)"
mumbai "cd ~/market-ha && sudo docker build -q -t market-api:$TAG ./api && sudo docker build -q -t market-generator:$TAG ./generator"

echo "3/5  Copying the images Mumbai -> Hyderabad (through this PC)"
mumbai "sudo docker save market-api:$TAG market-generator:$TAG | gzip -1" | hyderabad "gunzip | sudo docker load"

echo "4/5  Checking both VMs have identical layers"
A=$(mumbai "$FP"); B=$(hyderabad "$FP")
echo "     Mumbai:    $(echo $A)"; echo "     Hyderabad: $(echo $B)"
[ "$A" = "$B" ] || { echo "Images differ, not deploying." >&2; exit 1; }

echo "5/5  Rolling the stack to version $TAG"
mumbai "cd ~/market-ha/swarm && sudo MARKET_TAG=$TAG docker stack deploy -c stack.yaml --resolve-image never market"
sleep 20
mumbai "sudo docker service ls --format '{{.Name}}  {{.Replicas}}  {{.Image}}'"
echo
echo "Done. Check everything with: scripts/status.sh"
echo "Old versions stay on disk; Mumbai's disk is nearly full, so remove old tags now and then:"
echo "  sudo docker image rm market-api:<old> market-generator:<old>   (on each VM)"
