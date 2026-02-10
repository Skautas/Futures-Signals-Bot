@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0stop_bot.ps1"
endlocal
