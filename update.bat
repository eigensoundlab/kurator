@echo off
rem Kurator - update on Windows: get the newest code (if this is a git clone),
rem upgrade all dependencies to their newest compatible versions, and re-check security.
setlocal EnableExtensions
cd /d "%~dp0"

if exist ".git" (
    where git >nul 2>nul
    if not errorlevel 1 (
        echo [Kurator] Getting the newest version from GitHub ...
        git pull --ff-only
        if errorlevel 1 echo [Kurator] git pull failed ^(local changes?^) - continuing with the dependency update.
    ) else (
        echo [Kurator] git not found - skipping code update. Download the newest release from
        echo           https://github.com/eigensoundlab/kurator
    )
) else (
    echo [Kurator] Not a git clone - to update the code, download the newest release from
    echo           https://github.com/eigensoundlab/kurator and unzip it over this folder.
)

if /i "%~1"=="nopause" (call "%~dp0install.bat" nopause) else (call "%~dp0install.bat" %1 nopause)
set "RC=%errorlevel%"
endlocal & (if /i not "%~1"=="nopause" pause) & exit /b %RC%
