# Opens the demo layout: 3 live views side by side on top, the control panel along the bottom.
# Run with 64-bit Windows PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\demo\open-views.ps1
. (Join-Path $PSScriptRoot "common.ps1")
$view = Join-Path $PSScriptRoot "mkt-view.sh"
$titles = "MUMBAI VM", "HYDERABAD VM", "MICRO VM (votes only)", "CONTROL PANEL"

# 1. Copy the view script to /var/tmp on all three machines (survives reboots; strip Windows line endings).
foreach ($node in $cfg.MUMBAI_NODE, $cfg.HYDERABAD_NODE, $cfg.MICRO_NODE) {
  Get-Content -Raw $view | & $ssh -o ConnectTimeout=10 -i $hosts[$node][0] $hosts[$node][1] "sed '1s/^\xEF\xBB\xBF//' | tr -d '\r' > /var/tmp/mkt-view.sh"
}
# 2. Close any earlier copies. Windows first (their reconnect loops would otherwise restart the views), then leftover ssh views.
Get-Process cmd, powershell -ErrorAction SilentlyContinue | Where-Object { $titles -contains $_.MainWindowTitle } | Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*mkt-*.cmd*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ssh.exe' -and $_.CommandLine -like '*mkt-view*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Each view reconnects by itself: keep-alives notice a dead link within ~15 s, then the .cmd loop retries every 3 s.
$opts = "-o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o ConnectTimeout=10"
# Ctrl+C stops watch, but the remote loop ignores Ctrl+C itself and starts watch again at once: Ctrl+C = refresh.
$view = "sudo bash -c 'trap : INT; while :; do watch -n 1 -t bash /var/tmp/mkt-view.sh; clear; done'"   # (reused name: the local file was already copied above)
$panes = @(
  @{ name = "1-mumbai";    title = $titles[0]; cmd = "`"$ssh`" $opts -t -i `"$($cfg.MUMBAI_KEY)`" $($cfg.MUMBAI_HOST) `"$view`"" },
  @{ name = "2-hyderabad"; title = $titles[1]; cmd = "`"$ssh`" $opts -t -i `"$($cfg.HYDERABAD_KEY)`" $($cfg.HYDERABAD_HOST) `"$view`"" },
  @{ name = "3-micro";     title = $titles[2]; cmd = "`"$ssh`" $opts -t -i `"$($cfg.MICRO_KEY)`" $($cfg.MICRO_HOST) `"$view`"" }
)

Add-Type @"
using System; using System.Runtime.InteropServices;
public class Win { [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int ht, bool r); }
"@
Add-Type -AssemblyName System.Windows.Forms
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$w = [int]($area.Width / 3)
$topH = [int]($area.Height * 0.72)

function Place($proc, $x, $y, $width, $height, $label) {
  for ($t = 0; $t -lt 50 -and $proc.MainWindowHandle -eq 0; $t++) { Start-Sleep -Milliseconds 100; $proc.Refresh() }
  if ($proc.MainWindowHandle -ne 0) { [Win]::MoveWindow($proc.MainWindowHandle, $x, $y, $width, $height, $true) | Out-Null; "${label}: opened and placed" }
  else { "${label}: opened (could not move)" }
}

# 3. One cmd window per machine. The .cmd launchers hold key paths, so they go to %TEMP%, not the repo.
$i = 0
foreach ($p in $panes) {
  $bat = Join-Path $env:TEMP "mkt-$($p.name).cmd"
  Set-Content -Path $bat -Encoding ascii -Value @("@echo off", "title $($p.title)", ":loop", $p.cmd, "echo.", "echo [connection lost at %TIME% - reconnecting in 3 s; close this window to stop]", "timeout /t 3 /nobreak >nul", "goto loop")
  $proc = Start-Process cmd.exe -ArgumentList "/c", "`"$bat`"" -PassThru
  Place $proc ($area.X + $i * $w) $area.Y $w $topH $p.title
  $i++
}

$panel = Join-Path $PSScriptRoot "control-panel.ps1"
$proc = Start-Process powershell.exe -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='CONTROL PANEL'; & '$panel'" -PassThru
Place $proc $area.X ($area.Y + $topH) $area.Width ($area.Height - $topH) "CONTROL PANEL"
