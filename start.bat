@echo off
chcp 65001 >nul
if exist "data\runtime_python.txt" goto use_runtime
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
goto done
:use_runtime
set /p RUNTIME_PY=<data\runtime_python.txt
"%RUNTIME_PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
:done
if errorlevel 1 pause
