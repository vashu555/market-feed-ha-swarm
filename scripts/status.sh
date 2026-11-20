#!/usr/bin/env bash
# Read-only health check of the whole setup. Changes nothing.
# Usage (from Git Bash on Windows, or WSL):   scripts/status.sh
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
source scripts/.env
SSH=${SSH_BIN:-ssh}
mumbai()    { "$SSH" -o ConnectTimeout=10 -i "$MUMBAI_KEY" "$MUMBAI_HOST" "$@"; }
hyderabad() { "$SSH" -o ConnectTimeout=10 -i "$HYDERABAD_KEY" "$HYDERABAD_HOST" "$@"; }

echo "== Swarm managers (expect 3 Ready; 1 Leader; micro-lb-2 is Drain)"
mumbai "sudo docker node ls --format '{{.Hostname}}  {{.Status}}  {{.Availability}}  {{.ManagerStatus}}'"

echo; echo "== Services (expect api 4/4, generator 1/1, haproxy 2/2, redis 1/1, tunnel 2/2)"
mumbai "sudo docker service ls --format '{{.Name}}  {{.Replicas}}  {{.Image}}'"

echo; echo "== Where each container runs"
mumbai "sudo docker stack ps market --filter desired-state=running --format '{{.Name}}  {{.Node}}  {{.CurrentState}}'"

echo; echo "== Cloudflare tunnel connectors (live links to Cloudflare per VM; 0 = not connected)"
for vm in mumbai hyderabad; do
  printf '  %-10s %s\n' "$vm" "$($vm "curl -s -m 2 http://127.0.0.1:20290/ready | grep -o '\"readyConnections\":[0-9]*' | cut -d: -f2" || true)"
done

echo; echo "== Public entry points (the tunnel name, then one HAProxy per VM)"
for u in $PUBLIC_URLS; do
  printf '%-32s lb-health=%s  ' "$u" "$(curl -s -m 5 "$u/lb-health" || echo DOWN)"
  curl -s -m 5 -D - -o /dev/null "$u/whoami" | grep -i '^x-lb-node' | tr -d '\r' || echo "no answer"
done

echo; echo "== Image layer fingerprints (Mumbai and Hyderabad must match)"
FP='for c in $(sudo docker ps -q -f name=market_api -f name=market_generator); do n=$(sudo docker inspect -f "{{.Name}}" $c | cut -d. -f1,2 | tr -d /); i=$(sudo docker inspect -f "{{.Image}}" $c); echo "  $n $(sudo docker image inspect -f "{{range .RootFS.Layers}}{{.}}{{end}}" $i | sha256sum | cut -c1-12)"; done'
echo " Mumbai:";    mumbai "$FP"
echo " Hyderabad:"; hyderabad "$FP"
