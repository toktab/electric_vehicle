@echo off
echo ================================================
echo   EV Charging System - Startup Script
echo ================================================
echo.

echo [92m📦 Step 0: Down Docker images...[0m
docker-compose down
echo.

REM Step 1: Build all Docker images
echo [92m📦 Step 1: Building Docker images...[0m
echo.

docker-compose build
if %errorlevel% neq 0 (
    echo [91m❌ Failed to build images[0m
    pause
    exit /b 1
)

echo.
echo [92m✅ All images built successfully![0m
echo.

REM Step 2: Start the system
echo [92m🚀 Step 2: Starting docker-compose...[0m
echo.

docker-compose up -d
if %errorlevel% neq 0 (
    echo [91m❌ Failed to start containers[0m
    pause
    exit /b 1
)

echo.
echo [92m✅ Containers started![0m
echo.

REM Step 3: Wait for services to initialize
echo [93m⏳ Step 3: Waiting for services to initialize (15 seconds)...[0m
timeout /t 15 /nobreak >nul
echo.

REM Step 4: Show status
echo [92m📊 Step 4: System Status[0m
echo.
docker ps --format "table {{.Names}}\t{{.Status}}" --filter "name=evcharging_"
echo.

echo ================================================
echo   System Started Successfully!
echo ================================================
echo.
echo [92m🌐 Access Points:[0m
echo   Dashboard:  http://localhost:8081
echo   Central API: http://localhost:8080/api/status
echo   Registry:    http://localhost:5001/list
echo.
echo [92m📝 Useful Commands:[0m
echo   docker-compose logs -f              # View all logs
echo   docker logs evcharging_central      # View Central logs
echo   docker logs evcharging_weather      # View Weather logs
echo   docker attach evcharging_cp_manager # Access CP Manager
echo   docker attach evcharging_driver_1   # Access Driver 1
echo.
echo [92m🛑 To stop:[0m
echo   docker-compose down
echo.
echo ================================================
echo   Press any key to view live logs...
echo ================================================
pause >nul

docker-compose logs -f