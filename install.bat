@echo off
rem Kurator - install or repair on Windows.
rem Creates its own virtual environment in .venv (your main Python is not touched),
rem installs the newest compatible versions of all dependencies and checks them for
rem known vulnerabilities. Safe to run again at any time.
rem   install.bat        core app
rem   install.bat ai     + optional AI features (PyTorch with NVIDIA GPU support if a GPU is found)
rem   install.bat 3d     + optional 3D add-on: Draco-compressed glTF, binary USD (DracoPy, usd-core)
rem   install.bat ai 3d  both
rem   install.bat fresh  start over: a new .venv with the best Python found (e.g. after installing another Python)
rem HEIC and PDF support (and the add-ons) are ready-made packages added one by one: if one has no build for
rem this Python yet, Kurator is installed without it and the end says what is missing and why.
setlocal EnableExtensions
cd /d "%~dp0"
set "WANT_AI="
set "WANT_3D="
set "FRESH="
set "DO_PAUSE=1"
for %%A in (%*) do (
    if /i "%%~A"=="ai" set "WANT_AI=1"
    if /i "%%~A"=="3d" set "WANT_3D=1"
    if /i "%%~A"=="fresh" set "FRESH=1"
    if /i "%%~A"=="nopause" set "DO_PAUSE="
)

rem The newest Python that every package has ready-made builds for comes first; a brand-new one last.
set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
    for %%V in (3.14 3.13 3.12 3.11) do (
        if not defined PY (
            py -%%V -c "import sys" >nul 2>nul
            if not errorlevel 1 set "PY=py -%%V"
        )
    )
    if not defined PY (
        py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=py -3"
    )
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    echo [Kurator] Python 3.11 or newer was not found.
    echo           Install it from https://www.python.org/downloads/ ^(3.12+ recommended^)
    echo           and tick "Add python.exe to PATH" during setup.
    goto :fail
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
if errorlevel 1 echo [Kurator] Note: Python 3.12+ is recommended - older versions can't read Windows file creation dates.

if defined FRESH if exist ".venv" (
    echo [Kurator] Starting over: removing .venv ^(your settings and catalog are kept elsewhere^) ...
    rmdir /s /q ".venv"
)
if not exist ".venv\Scripts\python.exe" (
    echo [Kurator] Creating virtual environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
set "VPY=.venv\Scripts\python.exe"

echo [Kurator] Updating pip ...
"%VPY%" -m pip install --upgrade --quiet pip
if errorlevel 1 goto :fail

echo [Kurator] Installing Kurator and the newest compatible dependencies ...
set "PARTS=formats"
if exist ".venv\Lib\site-packages\open_clip" set "WANT_AI=1"
if exist ".venv\Lib\site-packages\pxr" set "WANT_3D=1"
if defined WANT_3D set "PARTS=%PARTS% 3d"
if defined WANT_AI set "PARTS=%PARTS% ai"
"%VPY%" -m pip install --upgrade --upgrade-strategy eager --quiet ".[audit]"
if errorlevel 1 goto :fail
echo [Kurator] Adding HEIC and PDF support and the add-ons you chose ...
"%VPY%" tools\install_extras.py %PARTS%
if defined WANT_3D if exist ".venv\Lib\site-packages\pxr" echo [Kurator] 3D add-on installed: Draco glTF and binary USD can be read.

if not defined WANT_AI goto :audit
"%VPY%" -c "import torch" >nul 2>nul
if errorlevel 1 goto :audit
where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo [Kurator] No NVIDIA GPU found - AI runs on the CPU ^(slower, but works^).
    goto :audit
)
"%VPY%" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
if not errorlevel 1 goto :audit
echo [Kurator] Installing PyTorch with NVIDIA CUDA support ^(large download^) ...
for %%C in (cu132 cu130 cu128) do (
    "%VPY%" -m pip install --upgrade --quiet torch torchvision --index-url https://download.pytorch.org/whl/%%C
    "%VPY%" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
    if not errorlevel 1 (
        echo [Kurator] GPU support ready ^(%%C^).
        goto :audit
    )
)
echo [Kurator] WARNING: could not enable the GPU. See https://pytorch.org/get-started/locally/ - AI still works on the CPU.

:audit
echo [Kurator] Checking installed packages for known vulnerabilities (pip-audit) ...
"%VPY%" tools\audit_deps.py
if errorlevel 1 (
    echo [Kurator] WARNING: pip-audit reported a problem ^(see above^). Run update.bat later to get fixed versions.
)

echo.
echo [Kurator] Ready. Start it with:  kurator.bat
echo           or with a folder:       kurator.bat --library "D:\Art"
if defined DO_PAUSE pause
endlocal
exit /b 0

:fail
echo [Kurator] Installation failed.
if defined DO_PAUSE pause
endlocal
exit /b 1
