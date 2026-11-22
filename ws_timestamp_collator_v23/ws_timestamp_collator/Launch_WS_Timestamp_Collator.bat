@echo off
setlocal
cd /d "%~dp0"

set APP_VERSION=v23

echo ============================================
echo  WS Timestamp Collator %APP_VERSION%
echo  Running from: %cd%
echo ============================================
echo.

REM Shared venv location, deliberately OUTSIDE this version folder. Every
REM ws_timestamp_collator_vX folder you ever extract points at the same
REM environment, so re-downloading or re-extracting a new version doesn't
REM force a full reinstall - only a genuinely fresh environment does.
set VENV_DIR=%USERPROFILE%\.ws_tools\ws_timestamp_collator_venv

if exist "%VENV_DIR%\pyvenv.cfg" (
    if exist "%VENV_DIR%\Scripts\activate.bat" (
        echo Existing shared environment found - checking dependencies...
        call "%VENV_DIR%\Scripts\activate.bat"
        pip install -r requirements.txt --quiet
        goto :launch
    ) else (
        echo Found an environment folder but it looks incomplete - rebuilding it.
        rmdir /s /q "%VENV_DIR%"
    )
) else (
    if exist "%VENV_DIR%" (
        echo Found a folder at the venv location but it isn't a valid Python environment - rebuilding it.
        rmdir /s /q "%VENV_DIR%"
    ) else (
        echo No shared environment found yet - setting one up now. This only happens once, ever, regardless of how many versions of this app you download later.
    )
)

echo Setting up environment at %VENV_DIR% ...
python -m venv "%VENV_DIR%"
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo.
    echo ERROR: venv creation failed - activate.bat was not created.
    echo Check that "python" on your PATH is a real Python install ^(not the
    echo Microsoft Store alias^) and try again.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
echo Installing dependencies...
pip install --upgrade pip >nul
pip install -r requirements.txt

:launch
call "%VENV_DIR%\Scripts\activate.bat"
echo.
echo Launching WS Timestamp Collator...
streamlit run App.py

pause
