@echo off
chcp 65001 >nul
py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
if errorlevel 1 pause 