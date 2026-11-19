# Demo control panel: break something on purpose, then watch the 3 view windows repair it.
# Only touches the market lab's containers. Nothing else on the VMs is affected.
. (Join-Path $PSScriptRoot "common.ps1")
$MUM = $cfg.MUMBAI_NODE; $HYD = $cfg.HYDERABAD_NODE

function NodeOf($svc) { (Leader "sudo docker service ps $svc --filter desired-state=running --format '{{.Node}}'" | Select-Object -First 1).Trim() }

function StopOne($svc, $slotFilter, $label) {
  $node = if ($slotFilter) { $slotFilter.Node } else { NodeOf $svc }
  $name = if ($slotFilter) { $slotFilter.Name } else { $svc }
  Write-Host ("[{0}] Stopping {1} on {2} ..." -f (Get-Date -Format HH:mm:ss), $label, $hosts[$node][2]) -ForegroundColor Yellow
  # No double quotes inside the remote command: Windows PowerShell 5.1 mangles them when calling ssh.exe.
  On $node "sudo docker ps -q -f name=$name | head -1 | xargs -r -I{} sh -c 'sudo docker stop -t 3 {} >/dev/null && echo stopped container {}'"
  Write-Host "Now watch the windows: it disappears, Swarm notices, a new copy starts." -ForegroundColor Green
}

while ($true) {
  Write-Host ""
  Write-Host "================ DEMO CONTROL PANEL (market lab only) ================" -ForegroundColor Cyan
  Write-Host " 1  Stop api-3 (Hyderabad)        -> watch: HAProxy marks it DOWN, Swarm starts a new api-3"
  Write-Host " 2  Stop api-1 (Mumbai)           -> same, on Mumbai"
  Write-Host " 3  Stop the generator            -> ticks pause ~3 s, then restart"
  Write-Host " 4  Stop Redis                    -> ticks stop, APIs go DOWN in HAProxy, then recover"
  Write-Host " 5  Stop Hyderabad's HAProxy      -> Hyderabad's address fails until Swarm restarts it"
  Write-Host " 6  Rolling restart of all 4 APIs -> one at a time, 3 keep serving (like a release)"
  Write-Host " 7  'Kill' Hyderabad: DRAIN it    -> everything moves off Hyderabad (VM failure, no power-off)"
  Write-Host " 8  Bring Hyderabad back (ACTIVE) -> its APIs + HAProxy return"
  Write-Host " Q  Quit"
  $c = Read-Host "Choose"
  switch ($c.ToUpper()) {
    "1" { StopOne "market_api" @{Node=$HYD; Name="market_api.3."} "api-3" }
    "2" { StopOne "market_api" @{Node=$MUM; Name="market_api.1."} "api-1" }
    "3" { StopOne "market_generator" $null "the generator" }
    "4" { StopOne "market_redis" $null "Redis" }
    "5" { StopOne "market_haproxy" @{Node=$HYD; Name="market_haproxy."} "Hyderabad's HAProxy" }
    "6" { Write-Host "Rolling restart of market_api ..." -ForegroundColor Yellow
          Leader "sudo docker service update --force --detach market_api >/dev/null && echo started: APIs will be replaced one by one" }
    "7" { $ok = Read-Host "Hyderabad's address will stop answering until you choose 8. Type YES to drain"
          if ($ok -eq "YES") { Leader "sudo docker node update --availability drain $HYD >/dev/null && echo Hyderabad drained" } }
    "8" { Leader "sudo docker node update --availability active $HYD >/dev/null && echo Hyderabad active again" }
    "Q" { exit }
    default { Write-Host "Choose 1-8 or Q" }
  }
}
