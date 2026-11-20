# Demo control panel: break something on purpose, then watch the 3 view windows repair it.
# Only touches the market lab's containers and the voter's Docker. Nothing else on the VMs is affected.
. (Join-Path $PSScriptRoot "common.ps1")
$MUM = $cfg.MUMBAI_NODE; $HYD = $cfg.HYDERABAD_NODE; $MIC = $cfg.MICRO_NODE

# Run a Swarm command on whichever manager answers first (Mumbai, then Hyderabad, then the micro VM),
# so the panel keeps working even when one machine is down.
function Mgr($cmd) {
  foreach ($n in $MUM, $HYD, $MIC) {
    $out = On $n "sudo docker node ls -q >/dev/null 2>&1 && $cmd"
    if ($LASTEXITCODE -eq 0) { return $out }
  }
  Write-Host "No manager answered (is a majority down?)" -ForegroundColor Red
}
function NodeOf($svc) { (Mgr "sudo docker service ps $svc --filter desired-state=running --format '{{.Node}}'" | Select-Object -First 1).Trim() }

function StopOne($svc, $slotFilter, $label) {
  $node = if ($slotFilter) { $slotFilter.Node } else { NodeOf $svc }
  $name = if ($slotFilter) { $slotFilter.Name } else { $svc }
  Write-Host ("[{0}] Stopping {1} on {2} ..." -f (Get-Date -Format HH:mm:ss), $label, $hosts[$node][2]) -ForegroundColor Yellow
  # No double quotes inside remote commands: Windows PowerShell 5.1 mangles them when calling ssh.exe.
  On $node "sudo docker ps -q -f name=$name | head -1 | xargs -r -I{} sh -c 'sudo docker stop -t 3 {} >/dev/null && echo stopped container {}'"
  Write-Host "Now watch the windows: it disappears, Swarm notices, a new copy starts." -ForegroundColor Green
}

function Status {
  Write-Host "--- Managers (Leader decides; 2 of 3 must agree)" -ForegroundColor Cyan
  Mgr "sudo docker node ls --format '{{.Hostname}}  {{.Status}}  {{.Availability}}  {{.ManagerStatus}}'"
  Write-Host "--- Services" -ForegroundColor Cyan
  Mgr "sudo docker service ls --format '{{.Name}}  {{.Replicas}}'"
  Write-Host "--- Where each container runs" -ForegroundColor Cyan
  Mgr "sudo docker stack ps market --filter desired-state=running --format '{{.Name}}  {{.Node}}  {{.CurrentState}}'"
}

while ($true) {
  Write-Host ""
  Write-Host "================ DEMO CONTROL PANEL (market lab only) ================" -ForegroundColor Cyan
  Write-Host " S  Status: managers, leader, services, placement"
  Write-Host " D  Open the public dashboards in the browser (PUBLIC_URLS in scripts\.env)"
  Write-Host " --- break a container ---"
  Write-Host " 1  Stop api-3 (Hyderabad)        -> HAProxy marks it DOWN, Swarm starts a new api-3"
  Write-Host " 2  Stop api-1 (Mumbai)           -> same, on Mumbai"
  Write-Host " 3  Stop the generator            -> ticks pause ~3 s, then restart"
  Write-Host " 4  Stop Redis                    -> ticks stop, APIs go DOWN in HAProxy, then recover"
  Write-Host " 5  Stop Hyderabad's HAProxy      -> Hyderabad's address fails until Swarm restarts it"
  Write-Host " 6  Rolling restart of all 4 APIs -> one at a time, 3 keep serving (like a release)"
  Write-Host " T  Stop Mumbai's tunnel connector -> https://market.<your-domain> keeps working via Hyderabad's"
  Write-Host " --- lose a machine ---"
  Write-Host " 7  'Kill' Hyderabad: DRAIN it    -> everything moves to Mumbai (VM failure, no power-off)"
  Write-Host " 8  Bring Hyderabad back (ACTIVE) -> its APIs + HAProxy return"
  Write-Host " 9  Stop the VOTER (micro VM)     -> 2 of 3 managers left: still a majority, app unaffected"
  Write-Host " 10 Start the voter again         -> it rejoins quietly as a follower"
  Write-Host " Q  Quit"
  $c = Read-Host "Choose"
  switch ($c.ToUpper()) {
    "S" { Status }
    "D" { foreach ($u in ($cfg.PUBLIC_URLS -split '\s+')) { if ($u) { Start-Process $u } } }
    "1" { StopOne "market_api" @{Node=$HYD; Name="market_api.3."} "api-3" }
    "2" { StopOne "market_api" @{Node=$MUM; Name="market_api.1."} "api-1" }
    "3" { StopOne "market_generator" $null "the generator" }
    "4" { StopOne "market_redis" $null "Redis" }
    "5" { StopOne "market_haproxy" @{Node=$HYD; Name="market_haproxy."} "Hyderabad's HAProxy" }
    "T" { StopOne "market_tunnel" @{Node=$MUM; Name="market_tunnel."} "Mumbai's Cloudflare Tunnel connector" }
    "6" { Write-Host "Rolling restart of market_api ..." -ForegroundColor Yellow
          Mgr "sudo docker service update --force --detach market_api >/dev/null && echo started: APIs will be replaced one by one" }
    "7" { $ok = Read-Host "Hyderabad's address will stop answering until you choose 8. Type YES to drain"
          if ($ok -eq "YES") { Mgr "sudo docker node update --availability drain $HYD >/dev/null && echo Hyderabad drained" } }
    "8" { Mgr "sudo docker node update --availability active $HYD >/dev/null && echo Hyderabad active again" }
    "9" { $ok = Read-Host "The voter goes offline until you choose 10 (if a VM also fails meanwhile, repairs stop). Type YES"
          if ($ok -eq "YES") { On $MIC "sudo systemctl stop docker && echo voter Docker stopped: watch micro-lb-2 turn Down/Unreachable"; Start-Sleep 5; Status } }
    "10" { Write-Host "Starting the voter's Docker (it first waits until a VM answers through NetBird) ..." -ForegroundColor Yellow
           On $MIC "sudo systemctl start docker && echo voter Docker running"; Start-Sleep 5; Status }
    "Q" { exit }
    default { Write-Host "Choose S, D, 1-10, T or Q" }
  }
}
