@echo off
cd /d "%~dp0"
python build.py
if errorlevel 1 (pause & exit /b 1)
start "" "dist\index.html"
