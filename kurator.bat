@echo off
rem Start Kurator. Any options are passed on, e.g.:  kurator.bat --library "D:\Art" --port 8766
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [Kurator] Not installed yet - running install.bat first ...
    call "%~dp0install.bat" nopause
    if errorlevel 1 ( pause & exit /b 1 )
)
".venv\Scripts\python.exe" -m kurator %*
endlocal
