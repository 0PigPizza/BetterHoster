@echo off
cd /d "%~dp0"
docker compose up -d --build
if errorlevel 1 exit /b %errorlevel%
echo BetterHoster is available at http://127.0.0.1:8000/
