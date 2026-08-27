@echo off
title RabbitMQ Docker Manager
color 0A

echo ============================================
echo   RabbitMQ Docker Manager
echo ============================================
echo.

:: --- Check if Docker is running ---
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not running! Please start Docker Desktop first.
    pause
    exit /b 1
)

:: --- Stop and remove existing container if exists ---
echo [1/4] Checking existing container...
docker rm -f rabbitmqs >nul 2>&1
if errorlevel 1 (
    echo       No existing container found, continuing...
) else (
    echo       Old container successfully removed.
)

:: --- Build image ---
echo.
echo [2/4] Building image custom-rabbitmqs...
docker build -t custom-rabbitmqs .
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed! Check your Dockerfile and supporting files.
    pause
    exit /b 1
)
echo       Build successful!

:: --- Run container ---
echo.
echo [3/4] Running container...
docker run -d ^
    --name rabbitmqs ^
    -p 5672:5672 ^
    -p 15672:15672 ^
    -p 8883:8883 ^
    -p 1883:1883 ^
    -p 15675:15675 ^
    custom-rabbitmqs
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
docker update --restart unless-stopped rabbitmqs >nul
echo       Auto-start enabled.

:: --- Done ---
echo.
echo ============================================
echo   RabbitMQ is running!
echo ============================================
echo.
echo   AMQP       : localhost:5672
echo   Management : http://localhost:15672
echo   MQTT       : localhost:1883
echo   MQTT SSL   : localhost:8883
echo   Web MQTT   : localhost:15675
echo.
echo   Default login: guest / guest
echo.
pause