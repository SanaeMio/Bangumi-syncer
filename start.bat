@echo off
chcp 65001 >nul
echo ================================
echo          Bangumi-Syncer         
echo ================================
echo.
echo 正在启动Web服务器...
echo 启动成功后请访问: http://localhost:8000
echo.
uvicorn bangumi_sync:app --host 0.0.0.0 --port 8000
pause 