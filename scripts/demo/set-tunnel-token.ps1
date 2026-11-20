# Stores the Cloudflare Tunnel token as the Swarm secret "cf_tunnel_token". Run it once, yourself:
#   powershell -ExecutionPolicy Bypass -File scripts\demo\set-tunnel-token.ps1
# The token is typed (or pasted) at a hidden prompt and sent over SSH on standard input, so it never
# shows on screen, in a file, in shell history or in a process list. Swarm keeps it encrypted.
. (Join-Path $PSScriptRoot "common.ps1")

$secure = Read-Host "Paste the tunnel token (or the whole 'cloudflared ... install <token>' command); it stays hidden" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
# Tokens are base64 JSON, so they start with "eyJ". Pull just the token out of a pasted install command.
$m = [regex]::Match($plain, 'eyJ[A-Za-z0-9+/=_-]{40,}')
$plain = $null
if (-not $m.Success) { Write-Host "That does not look like a tunnel token (it should start with eyJ). Nothing was changed." -ForegroundColor Red; exit 1 }

foreach ($n in $cfg.MUMBAI_NODE, $cfg.HYDERABAD_NODE) {      # any manager can create a secret
  $exists = On $n "sudo docker secret ls --format '{{.Name}}' | grep -qx cf_tunnel_token && echo yes || echo no"
  if ($LASTEXITCODE -ne 0) { continue }
  if ($exists -eq "yes") {
    Write-Host "cf_tunnel_token already exists. Secrets can't be changed: to rotate, see docs/CLOUDFLARE-TUNNEL.md." -ForegroundColor Yellow
    exit 1
  }
  $m.Value | & $ssh -o ConnectTimeout=10 -i $hosts[$n][0] $hosts[$n][1] "tr -d '\r\n' | sudo docker secret create cf_tunnel_token - >/dev/null && echo created"
  if ($LASTEXITCODE -eq 0) { Write-Host "Swarm secret cf_tunnel_token created (via $($hosts[$n][2])). Now deploy the stack." -ForegroundColor Green; exit 0 }
}
Write-Host "No manager accepted the secret. Nothing was changed." -ForegroundColor Red
exit 1
