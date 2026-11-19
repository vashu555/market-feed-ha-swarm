@echo off
rem ============================================================================
rem  One click: opens the demo layout
rem    - 3 live views on top: MUMBAI VM, HYDERABAD VM, MICRO VM (votes only)
rem    - CONTROL PANEL along the bottom (break things, watch Swarm repair them)
rem  Needs scripts\.env filled in (copy scripts\env.example). Run it again any
rem  time: it closes the old windows first, so nothing is duplicated.
rem ============================================================================
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
rem From a 32-bit shell, System32 is redirected; Sysnative reaches the real 64-bit PowerShell.
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%~dp0scripts\.env" (
  echo Missing scripts\.env - copy scripts\env.example to scripts\.env and fill in the SSH keys and IPs.
  pause
  exit /b 1
)

echo Opening the market demo windows...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\demo\open-views.ps1"
