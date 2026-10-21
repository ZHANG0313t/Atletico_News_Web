@echo off
chcp 65001 >nul
echo 仅允许当前 WiFi 网卡、同一子网访问新闻站TCP 8765。
echo Windows会请求管理员权限，请核对授权窗口。
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""%~dp0tools\configure_lan_firewall.ps1""' -Wait"
echo 请查看项目docs目录中的 firewall-result.json 确认结果。
pause
