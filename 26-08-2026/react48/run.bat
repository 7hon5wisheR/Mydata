@echo off
title React App Docker Manager
color 0B
echo ============================================
echo   React App Docker Manager
echo ============================================
echo.
:: --- Configuration (edit as needed) ---
set IMAGE_NAME=clientstatus
set CONTAINER_NAME=clientstatus
set HOST_IP=192.168.137.1
set NUMBER_OF_CABINETS=2

:: --- Data folder on the HOST (survives docker rm / rebuild) ---
:: broker-config.json and registry-data.json live here, on your
:: Windows machine, NOT inside the container's own filesystem.
:: Editing broker-config.json here (even while the container is
:: running) takes effect immediately, because it's bind-mounted
:: into the container below -- it's the SAME file, not a copy.
set DATA_DIR=%~dp0data

:: --- Check Docker ---
docker info >nul 2>&1
if errorlevel 1 (
echo [ERROR] Docker is not running! Please start Docker Desktop first.
pause
exit /b 1
)

:: --- Make sure the host data folder + seed files exist ---
:: (only created the FIRST time you run this; won't overwrite
:: an existing broker-config.json / registry-data.json)
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
if not exist "%DATA_DIR%\broker-config.json" (
  echo {"ip": "20.81.43.213"} > "%DATA_DIR%\broker-config.json"
  echo       Created default %DATA_DIR%\broker-config.json
)
if not exist "%DATA_DIR%\registry-data.json" (
  echo {} > "%DATA_DIR%\registry-data.json"
  echo       Created empty %DATA_DIR%\registry-data.json
)

:: --- Stop and remove old container ---
echo [1/4] Checking for existing container...
docker rm -f %CONTAINER_NAME% >nul 2>&1
if errorlevel 1 (
echo       No existing container found, continuing...
) else (
echo       Existing container removed successfully.
)

:: --- Build image (npm install runs automatically inside Dockerfile) ---
echo.
echo [2/4] Building image %IMAGE_NAME%...
echo       (npm install runs automatically inside Docker, please wait...)
echo.
docker build -t %IMAGE_NAME% .
if errorlevel 1 (
echo.
echo [ERROR] Build failed! Check your Dockerfile and ensure package.json exists.
pause
exit /b 1
)
echo       Build completed successfully!

:: --- Run container ---
echo.
echo [3/4] Starting container...
docker run -d ^
--name %CONTAINER_NAME% ^
-p 3000:3000 ^
-p 3001:3001 ^
-e HOST_IP=%HOST_IP% ^
-e NUMBER_OF_CABINETS=%NUMBER_OF_CABINETS% ^
-v "%DATA_DIR%\broker-config.json:/app/broker-config.json" ^
-v "%DATA_DIR%\registry-data.json:/app/registry-data.json" ^
%IMAGE_NAME%
if errorlevel 1 (
echo.
echo [ERROR] Failed to start container!
pause
exit /b 1
)
echo       Container started successfully!

:: --- Set auto restart ---
echo.
echo [4/4] Setting auto-start on reboot...
docker update --restart unless-stopped %CONTAINER_NAME% >nul
echo       Auto-start enabled.

:: --- Done ---
echo.
echo ============================================
echo   Application is running successfully!
echo ============================================
echo.
echo   React Frontend : http://localhost:3000
echo   Backend Server : http://localhost:3001
echo.
echo   HOST_IP            : %HOST_IP%
echo   NUMBER_OF_CABINETS : %NUMBER_OF_CABINETS%
echo   Broker config file : %DATA_DIR%\broker-config.json
echo.
echo   [TIP] To change the MQTT broker IP WITHOUT restarting or
echo         rebuilding anything, use the small settings dot in
echo         the bottom-right corner of the web UI, or edit and
echo         save: %DATA_DIR%\broker-config.json
echo.
echo   [TIP] To change HOST_IP or NUMBER_OF_CABINETS, edit the
echo         "Configuration" section at the top of this run.bat
echo         and run it again.
echo.
pause
