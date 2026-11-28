@echo off
rem Double-click: sets up the load test (first time only) and opens its page in your browser.
cd /d "%~dp0"
title Market load test (close this window to quit)
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
%PY% --version >nul 2>&1 || (
  echo Python 3.10 or newer is needed: https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
  pause & exit /b 1
)
if not exist venv\Scripts\python.exe (
  echo First run: installing the load-test libraries ^(about 1 minute^)...
  %PY% -m venv venv || (pause & exit /b 1)
  venv\Scripts\python -m pip install --quiet --upgrade pip
  venv\Scripts\python -m pip install --quiet -r requirements.txt || (pause & exit /b 1)
)
venv\Scripts\python ui.py %*
pause
