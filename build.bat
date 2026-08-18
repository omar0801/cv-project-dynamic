@echo off
setlocal

REM Build the Windows executable. Run from the repo root with the venv activated.

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo pyinstaller not found. Activate your venv and run: pip install -r requirements.txt
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

pyinstaller app.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: dist\CVBuilder\CVBuilder.exe
endlocal
