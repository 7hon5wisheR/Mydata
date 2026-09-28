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

:: --- Check Docker ---
docker info >nul 2>&1
if errorlevel 1 (
echo [ERROR] Docker is not running! Please start Docker Desktop first.
pause
exit /b 1
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
echo.
echo   [TIP] To change the IP or number of cabinets,
echo         edit the "Configuration" section in this run.bat file.
echo.
pause
