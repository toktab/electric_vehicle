@echo off
echo ================================================
echo   EV Charging System - Startup Script
echo ================================================
echo.

REM Step 1: Build all Docker images using docker-compose
echo [92m📦 Step 1: Building Docker images...[0m
echo.

docker-compose build
if %errorlevel% neq 0 (
    echo [91m❌ Failed to build images[0m
    exit /b 1
)

echo.
echo [92m✅ All images built successfully![0m
echo.

REM Step 2: Start the system
echo [92m🚀 Step 2: Starting docker-compose...[0m
echo.

docker-compose up -d
docker-compose logs -f

echo.
echo ================================================
echo   System Started Successfully!
echo ================================================
echo.
echo Useful commands:
echo   docker-compose logs -f          # View all logs
echo   docker attach evcharging_central  # Access Central console
echo   docker attach evcharging_cp_manager  # Access CP Manager
echo   docker attach evcharging_driver_1  # Access Driver 1
echo.
echo To stop: docker-compose down
echo ================================================