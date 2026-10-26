@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONPYCACHEPREFIX=__RUNTIME_ROOT__\pycache
python "%~dp0capture.py" --seconds 120
pause
