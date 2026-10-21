@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo Atletico新闻站 - 局域网共享模式
echo 正在检查服务状态，请稍候。
if exist "E:\Anaconda3\python.exe" (
    "E:\Anaconda3\python.exe" -B -u "%~dp0tools\launch_news_station.py" --lan
) else (
    python -B -u "%~dp0tools\launch_news_station.py" --lan
)
echo.
echo 以上是运行结果。按任意键关闭此窗口。
pause >nul
endlocal
