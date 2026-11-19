#!/bin/bash
# Read-only live view of the market Swarm, as seen from THIS machine.
# open-views.ps1 copies it to /tmp on each machine and runs:  watch -n 1 -t bash /tmp/mkt-view.sh
REGION=$(docker node inspect self --format '{{index .Spec.Labels "region"}}' 2>/dev/null)
case $REGION in
  mumbai)    ROLE="MUMBAI VM";;
  hyderabad) ROLE="HYDERABAD VM";;
  *)         ROLE="MICRO VM (drained: votes only)";;
esac
H=$(hostname)
short() { sed -E 's/(market_[a-z]+\.[0-9a-z]{1,6})[0-9a-z]*(\.[0-9a-z]+)?/\1/g'; }
echo "===== $ROLE  $(TZ=Asia/Kolkata date +%T) ====="
echo "--- Swarm managers ---"
docker node ls --format '{{.Hostname}} | {{.Status}} | {{.Availability}} | {{.ManagerStatus}}' | sed "s/^\($H\) /\1 (ME) /"
echo "--- Services: $(docker service ls --format '{{.Name}} {{.Replicas}}' | sed 's/ (max 2 per node)//; s/market_//' | tr '\n' ' ')"
echo "--- Every API copy, as Swarm sees it ---"
docker service ps market_api --filter desired-state=running --format '{{.Name}}  {{.Node}}  {{.CurrentState}}' | sort
echo "--- Containers ON THIS MACHINE ---"
out=$(docker ps --filter name=market_ --format '{{.Names}}  {{.Status}}' | short | sort)
if [ -n "$out" ]; then echo "$out"; else echo "(none: Swarm puts no containers here)"; fi
if docker ps -q --filter name=market_haproxy | grep -q .; then
  echo "--- This VM's HAProxy: which API slots are UP ---"
  curl -s -m 1 "http://127.0.0.1:18404/stats;csv" | awk -F, '$1=="api_pool" && $2!="BACKEND" && $18!~/MAINT/ {printf "%s=%s  ", $2, $18} END {print ""}'
  U=http://127.0.0.1:18080
else
  # No HAProxy here (the PC): ask the current Leader's HAProxy over NetBird.
  LEADER=$(docker node ls --filter role=manager -q | xargs docker node inspect \
           --format '{{.ManagerStatus.Leader}} {{.ManagerStatus.Addr}}' | awk '$1=="true"{split($2,a,":"); print a[1]}')
  U=http://$LEADER:18080
fi
echo "--- Live feed: $(curl -s -m 1 $U/whoami | grep -o '"served_by":"[^"]*"\|"last_market_sequence_here":[0-9]*' | sed 's/"served_by"://; s/"last_market_sequence_here":/tick #/' | tr '\n' ' ')"
echo "--- Events ON THIS MACHINE (last 3 min) ---"
ev=$(docker events --since 3m --until "$(date +%s)" --filter type=container --filter event=start --filter event=die --filter event=health_status \
     --format '{{.Time}} {{.Action}} {{.Actor.Attributes.name}}' 2>/dev/null | grep market_ | tail -6 \
     | while read t a n; do echo "$(TZ=Asia/Kolkata date -d @$t +%T)  $a  $n"; done | short)
if [ -n "$ev" ]; then echo "$ev"; else echo "(nothing happened here)"; fi
echo "--- NetBird peers: $(netbird status 2>/dev/null | grep 'Peers count' | sed 's/Peers count: //')"
