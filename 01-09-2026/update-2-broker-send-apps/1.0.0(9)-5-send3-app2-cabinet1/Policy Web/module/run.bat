@echo off
title ModuleHost Launcher
cd /d "%~dp0"

:: ── Cek Admin ──────────────────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo need as Administrator...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo  ModuleHost Setup ^& Launcher
echo ============================================

:: ══════════════════════════════════════════════
:: STEP 1: Activated WINDOWS AUTO-LOGIN
:: ══════════════════════════════════════════════
echo [1/4] Setting up Windows Auto-Login...

for /f "tokens=2 delims=\" %%u in ('whoami') do set WIN_USER=%%u

echo.
echo User: %WIN_USER%
set /p WIN_PASS=Enter password Windows (leave it blank if nothing password): 

reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon  /t REG_SZ /d "1"        /f >nul
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultUserName /t REG_SZ /d "%WIN_USER%" /f >nul
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword /t REG_SZ /d "%WIN_PASS%" /f >nul
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v ForceAutoLogon  /t REG_SZ /d "1"        /f >nul

echo [1/4] Auto-login active OK

:: ══════════════════════════════════════════════
:: STEP 2: windows_agent.py to TASK SCHEDULER
:: ══════════════════════════════════════════════
echo [2/4] Registering windows_agent.py to Task Scheduler...

for /f "delims=" %%p in ('where python') do (
    set PYTHON_EXE=%%p
    goto :found_python
)
:found_python

if not defined PYTHON_EXE (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

echo [2/4] Python: %PYTHON_EXE%

schtasks /delete /tn "WindowsAgent" /f >nul 2>&1
schtasks /create /tn "WindowsAgent" ^
    /tr "\"%PYTHON_EXE%\" \"%~dp0windows_agent.py\"" ^
    /sc onlogon ^
    /ru "%WIN_USER%" ^
    /rl highest ^
    /delay 0000:20 ^
    /f >nul

if errorlevel 1 (
    echo [ERROR] Failed register Task Scheduler!
    pause
    exit /b 1
)
echo [2/4] Task Scheduler (windows_agent) OK

:: ══════════════════════════════════════════════
:: STEP 3: BUILD & RUN DOCKER
:: ══════════════════════════════════════════════
echo [3/4] Building Docker image...
docker build -t utilityshutdown .
if errorlevel 1 (
    echo [ERROR] Docker build failed!
    pause
    exit /b 1
)


for /f %%h in ('hostname') do set WIN_HOSTNAME=%%h
echo [3/4] Windows Hostname: %WIN_HOSTNAME%

echo [3/4] Starting Docker container...
docker rm -f utilityshutdown) >nul 2>&1
docker run -d --name utilityshutdown ^
  -p 5100:5100 ^
  -e WINDOWS_HOSTNAME=%WIN_HOSTNAME% ^
  -v "%~dp0module.json:/app/module.json" ^
  -v "%~dp0data:/app/data" ^
  -v "%~dp0templates:/app/templates" ^
  utilityshutdown

docker update --restart unless-stopped utilityshutdown
echo [3/4] Docker OK

:: ══════════════════════════════════════════════
:: STEP 4: RUN windows_agent.py now
:: ══════════════════════════════════════════════
echo [4/4] Starting windows_agent.py now...
tasklist | find /i "windows_agent" >nul 2>&1
if errorlevel 1 (
    start "" "%PYTHON_EXE%" "%~dp0windows_agent.py"
    echo [4/4] windows_agent.py started OK
) else (
    echo [4/4] windows_agent already running, skip.
)

echo.
echo ============================================
echo  FINISH! Sistem ready.
echo  http://localhost:5100
echo.
echo  When restart:
echo  - Windows auto-login automatic
echo  - windows_agent.py automatic run (Task Scheduler)
echo  - sensor.py + Flask (5100) run (Docker)
echo.
echo  If CMD closed:
echo  - Flask :5100 still running (in Docker)
echo  - Change Hostname need windows_agent.py
echo    -> run: python windows_agent.py
echo    -> or restart Docker
echo ============================================
pause
