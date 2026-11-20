#!/bin/bash
# Read-only live view of the market Swarm, as seen from THIS machine.
#   bash mkt-view.sh        -> print one snapshot
#   bash mkt-view.sh loop   -> redraw every second (open-views.ps1 runs this on each machine)
# Every command has a time limit, so one stuck call can never freeze the window.
if [ "$1" = loop ]; then
  # Not `watch` (it left ghost text in the Windows console) and no "clear screen" (Windows scrolls the old view away,
  # so every refresh jumps). Instead each frame is built first, then written OVER the previous one in place:
  # cursor to top-left, every line followed by "erase rest of line", then "erase everything below".
  # Trimmed to the window, so nothing wraps or scrolls. No flicker, no shifting, no leftovers.
  trap : INT                              # Ctrl+C interrupts the current frame only; the loop carries on = a refresh
  printf '\033[2J\033[?25l'               # clear once at start, hide the cursor
  while :; do
    frame=$(bash "$0" 2>&1)
    rows=$(tput lines 2>/dev/null || echo 40); cols=$(tput cols 2>/dev/null || echo 100)
    out=$(printf '%s\n' "$frame" | cut -c1-$((cols - 1)) | head -n $((rows - 1)) | sed 's/$/\x1b[K/')
    printf '\033[H%s\n\033[J' "$out"      # home, overwrite lines, erase below
    sleep 1
  done
fi
t() { timeout 3 "$@" 2>/dev/null; }
REGION=$(t docker node inspect self --format '{{index .Spec.Labels "region"}}')
case $REGION in
  mumbai)    ROLE="MUMBAI VM";;
  hyderabad) ROLE="HYDERABAD VM";;
  *)         ROLE="MICRO VM (drained: votes only)";;
esac
H=$(hostname)
short() { sed -E 's/(market_[a-z]+\.[0-9a-z]{1,6})[0-9a-z]*(\.[0-9a-z]+)?/\1/g'; }
orno() { if [ -n "$1" ]; then echo "$1"; else echo "(no answer in 3 s - Swarm busy or no leader)"; fi; }
echo "===== $ROLE   updated $(TZ=Asia/Kolkata date +%T) IST ====="
echo; echo "--- Swarm managers ---"
orno "$(t docker node ls --format '{{.Hostname}} | {{.Status}} | {{.Availability}} | {{.ManagerStatus}}' | sed "s/^\($H\) /\1 (ME) /")"
echo; echo "--- Services: $(t docker service ls --format '{{.Name}} {{.Replicas}}' | sed 's/ (max 2 per node)//; s/market_//' | tr '\n' ' ')"
echo; echo "--- Every API copy, as Swarm sees it ---"
orno "$(t docker service ps market_api --filter desired-state=running --format '{{.Name}}  {{.Node}}  {{.CurrentState}}' | sort)"
echo; echo "--- Containers ON THIS MACHINE ---"
out=$(t docker ps --filter name=market_ --format '{{.Names}}  {{.Status}}' | short | sort)
if [ -n "$out" ]; then echo "$out"; else echo "(none: Swarm puts no containers here)"; fi
if t docker ps -q --filter name=market_haproxy | grep -q .; then
  echo; echo "--- This VM's HAProxy: which API slots are UP ---"
  curl -s -m 1 "http://127.0.0.1:18404/stats;csv" | awk -F, '$1=="api_pool" && $2!="BACKEND" && $18!~/MAINT/ {printf "%s=%s  ", $2, $18} END {print ""}'
  U=http://127.0.0.1:18080
else
  # No HAProxy here (the voter): ask the current Leader's HAProxy over NetBird.
  LEADER=$(t docker node ls --filter role=manager -q | xargs -r timeout 3 docker node inspect \
           --format '{{.ManagerStatus.Leader}} {{.ManagerStatus.Addr}}' 2>/dev/null | awk '$1=="true"{split($2,a,":"); print a[1]}')
  U=http://${LEADER:-127.0.0.1}:18080
fi
if t docker ps -q --filter name=market_tunnel | grep -q .; then
  # cloudflared's /ready: {"status":200,"readyConnections":4,...} when linked to Cloudflare's edge.
  rc=$(curl -s -m 1 http://127.0.0.1:20290/ready | grep -o '"readyConnections":[0-9]*' | cut -d: -f2)
  echo; echo "--- Cloudflare tunnel connector: ${rc:-0} live links to Cloudflare $([ "${rc:-0}" -gt 0 ] && echo '(serving the public name)' || echo '(NOT connected)')"
fi
feed=$(curl -s -m 1 $U/whoami | grep -o '"served_by":"[^"]*"\|"last_market_sequence_here":[0-9]*' | sed 's/"served_by"://; s/"last_market_sequence_here":/tick #/' | tr '\n' ' ')
echo; echo "--- Live feed: ${feed:-(no answer)}"
echo; echo "--- Events ON THIS MACHINE (last 3 min) ---"
ev=$(t docker events --since 3m --until "$(date +%s)" --filter type=container --filter event=start --filter event=die --filter event=health_status \
     --format '{{.Time}} {{.Action}} {{.Actor.Attributes.name}}' | grep market_ | tail -4 \
     | while read ts a n; do echo "$(TZ=Asia/Kolkata date -d @$ts +%T)  $a  $n"; done | short)
if [ -n "$ev" ]; then echo "$ev"; else echo "(nothing happened here)"; fi
echo; echo "--- NetBird peers: $(t netbird status | grep 'Peers count' | sed 's/Peers count: //')"
