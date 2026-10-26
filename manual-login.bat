@echo off
chcp 65001 >nul
set PYTHONUTF8=1
python "%~dp0probe_web.py" --login
pause
