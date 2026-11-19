# Shared by control-panel.ps1 and open-views.ps1: reads scripts/.env (never committed) and defines SSH helpers.
# 32-bit PowerShell can't see System32's ssh.exe (Windows redirects it); Sysnative is the way through.
$sys = if (Test-Path "$env:SystemRoot\Sysnative") { "$env:SystemRoot\Sysnative" } else { "$env:SystemRoot\System32" }
$ssh = "$sys\OpenSSH\ssh.exe"
# Text piped to ssh.exe must be plain UTF-8: Windows PowerShell 5.1 otherwise prepends a byte-order mark,
# which breaks the first line (#!/bin/bash) of scripts copied to the VMs.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false

$envFile = Join-Path $PSScriptRoot "..\.env"
if (-not (Test-Path $envFile)) { Write-Host "Missing scripts/.env - copy scripts/env.example and fill it in." -ForegroundColor Red; exit 1 }
$cfg = @{}
Get-Content $envFile | Where-Object { $_ -match '^\s*([A-Z_]+)=(.*)$' } | ForEach-Object {
  $cfg[$Matches[1]] = $Matches[2].Trim().Trim('"')
}
if (-not $cfg.MUMBAI_NODE)    { $cfg.MUMBAI_NODE = "instance-20260319-0225" }
if (-not $cfg.HYDERABAD_NODE) { $cfg.HYDERABAD_NODE = "oci-hyderabad" }
if (-not $cfg.MICRO_NODE)     { $cfg.MICRO_NODE = "micro-lb-2" }

# Swarm node hostname -> SSH key, SSH target, display name
$hosts = @{
  $cfg.MUMBAI_NODE    = @($cfg.MUMBAI_KEY,    $cfg.MUMBAI_HOST,    "Mumbai")
  $cfg.HYDERABAD_NODE = @($cfg.HYDERABAD_KEY, $cfg.HYDERABAD_HOST, "Hyderabad")
  $cfg.MICRO_NODE     = @($cfg.MICRO_KEY,     $cfg.MICRO_HOST,     "Micro VM")
}
function On($node, $cmd) { $h = $hosts[$node]; & $ssh -o ConnectTimeout=10 -i $h[0] $h[1] $cmd }
function Leader($cmd)    { On $cfg.MUMBAI_NODE $cmd }   # any manager works; Mumbai is normally the Leader
