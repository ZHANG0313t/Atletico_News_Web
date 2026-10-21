@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Atletico新闻站启动中。
echo 网页地址：http://127.0.0.1:8765/
echo 使用期间请保持本窗口开启；按 Ctrl+C 可停止服务。
echo 如果提示端口被占用，请直接打开上面的网址，已有服务正在运行。
if exist "E:\Anaconda3\python.exe" (
    "E:\Anaconda3\python.exe" -B -u "%~dp0web_app.py"
) else (
    python -B -u "%~dp0web_app.py"
)
echo 服务已停止。可查看上方信息，按任意键关闭窗口。
pause >nul