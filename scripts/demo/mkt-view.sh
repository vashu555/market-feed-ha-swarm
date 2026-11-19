#!/bin/bash
# Read-only live view of the market Swarm, as seen from THIS machine.
# open-views.ps1 copies it to /var/tmp on each machine and runs:  watch -n 1 -t bash /var/tmp/mkt-view.sh
# Every command has a time limit, so one stuck call can never freeze the window.
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
echo "--- Swarm managers ---"
orno "$(t docker node ls --format '{{.Hostname}} | {{.Status}} | {{.Availability}} | {{.ManagerStatus}}' | sed "s/^\($H\) /\1 (ME) /")"
echo "--- Services: $(t docker service ls --format '{{.Name}} {{.Replicas}}' | sed 's/ (max 2 per node)//; s/market_//' | tr '\n' ' ')"
echo "--- Every API copy, as Swarm sees it ---"
orno "$(t docker service ps market_api --filter desired-state=running --format '{{.Name}}  {{.Node}}  {{.CurrentState}}' | sort)"
echo "--- Containers ON THIS MACHINE ---"
out=$(t docker ps --filter name=market_ --format '{{.Names}}  {{.Status}}' | short | sort)
if [ -n "$out" ]; then echo "$out"; else echo "(none: Swarm puts no containers here)"; fi
if t docker ps -q --filter name=market_haproxy | grep -q .; then
  echo "--- This VM's HAProxy: which API slots are UP ---"
  curl -s -m 1 "http://127.0.0.1:18404/stats;csv" | awk -F, '$1=="api_pool" && $2!="BACKEND" && $18!~/MAINT/ {printf "%s=%s  ", $2, $18} END {print ""}'
  U=http://127.0.0.1:18080
else
  # No HAProxy here (the voter): ask the current Leader's HAProxy over NetBird.
  LEADER=$(t docker node ls --filter role=manager -q | xargs -r timeout 3 docker node inspect \
           --format '{{.ManagerStatus.Leader}} {{.ManagerStatus.Addr}}' 2>/dev/null | awk '$1=="true"{split($2,a,":"); print a[1]}')
  U=http://${LEADER:-127.0.0.1}:18080
fi
feed=$(curl -s -m 1 $U/whoami | grep -o '"served_by":"[^"]*"\|"last_market_sequence_here":[0-9]*' | sed 's/"served_by"://; s/"last_market_sequence_here":/tick #/' | tr '\n' ' ')
echo "--- Live feed: ${feed:-(no answer)}"
echo "--- Events ON THIS MACHINE (last 3 min) ---"
ev=$(t docker events --since 3m --until "$(date +%s)" --filter type=container --filter event=start --filter event=die --filter event=health_status \
     --format '{{.Time}} {{.Action}} {{.Actor.Attributes.name}}' | grep market_ | tail -6 \
     | while read ts a n; do echo "$(TZ=Asia/Kolkata date -d @$ts +%T)  $a  $n"; done | short)
if [ -n "$ev" ]; then echo "$ev"; else echo "(nothing happened here)"; fi
echo "--- NetBird peers: $(t netbird status | grep 'Peers count' | sed 's/Peers count: //')"
