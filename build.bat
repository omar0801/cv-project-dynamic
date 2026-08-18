@echo off
setlocal

REM Build the Windows executable. Run from the repo root with the venv activated.

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo pyinstaller not found. Activate your venv and run: pip install -r requirements.txt
    exit /b 1
)

REM Windows locks the bundled DLLs while the app is running, which makes the
REM rebuild fail partway through with a wall of "Access is denied".
tasklist /fi "imagename eq CVBuilder.exe" 2>nul | find /i "CVBuilder.exe" >nul
if not errorlevel 1 (
    echo CVBuilder.exe is still running. Close it, then run this script again.
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM rmdir does not set errorlevel on a locked file, so check what it left behind.
if exist dist (
    echo Could not delete dist\. Close any program using files in that folder.
    exit /b 1
)

pyinstaller --noconfirm app.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: dist\CVBuilder\CVBuilder.exe
endlocal
