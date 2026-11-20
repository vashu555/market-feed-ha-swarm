#!/usr/bin/env bash
# Apply changes to swarm/stack.yaml or swarm/haproxy-swarm.cfg WITHOUT building new images.
# Keeps the app version that is running now. For code changes use scripts/release.sh instead.
#
# Usage (from Git Bash on Windows, or WSL):   scripts/deploy.sh
# Swarm only touches services whose settings changed; the others keep running untouched.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
source scripts/.env
SSH=${SSH_BIN:-ssh}
mumbai()    { "$SSH" -o ConnectTimeout=10 -i "$MUMBAI_KEY" "$MUMBAI_HOST" "$@"; }
hyderabad() { "$SSH" -o ConnectTimeout=10 -i "$HYDERABAD_KEY" "$HYDERABAD_HOST" "$@"; }

# The version running now, read from the API service (market-api:<tag>).
TAG=$(mumbai "sudo docker service inspect market_api --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'" | sed 's/.*://; s/@.*//')
[ -n "$TAG" ] || { echo "Could not read the running version from market_api." >&2; exit 1; }
echo "Running version: $TAG (kept)"

if grep -q 'cf_tunnel_token' swarm/stack.yaml && ! mumbai "sudo docker secret ls --format '{{.Name}}' | grep -qx cf_tunnel_token"; then
  echo "The Swarm secret cf_tunnel_token does not exist yet. Run scripts/demo/set-tunnel-token.ps1 first." >&2
  exit 1
fi

echo "1/2  Copying swarm/ to ~/market-ha on both VMs"
for vm in mumbai hyderabad; do
  tar -czf - swarm | $vm "mkdir -p ~/market-ha && tar -xzf - -C ~/market-ha"
done

echo "2/2  Deploying the stack"
mumbai "cd ~/market-ha/swarm && sudo MARKET_TAG=$TAG docker stack deploy -c stack.yaml --resolve-image never market"
sleep 15
mumbai "sudo docker service ls --format '{{.Name}}  {{.Replicas}}  {{.Image}}'"
echo
echo "Done. Check everything with: scripts/status.sh"
