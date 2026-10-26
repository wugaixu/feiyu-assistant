@echo off
rem Start the management app (console + tray) and open its window.
rem The workflow runs while the app runs; closing the window keeps it in the tray.
wscript.exe "%~dp0管理界面.vbs"
