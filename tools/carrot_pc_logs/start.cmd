@echo off
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0receiver.py" %*
) else (
  python "%~dp0receiver.py" %*
)
if errorlevel 1 pause
