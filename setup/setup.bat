@echo off
setlocal
echo ============================================================
echo   maimai DX Rating Clipper - Install
echo ============================================================
echo.

echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   Python not found. Installing via winget...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [ERROR] winget is not available.
        echo         Install Python 3.11 manually from https://www.python.org/downloads
        echo         and check "Add python.exe to PATH" during setup.
        pause
        exit /b 1
    )
    winget install Python.Python.3.11 --accept-source-agreements --accept-package-agreements
    echo.
    echo [OK] Python installed.
    echo      Close this window and run setup.bat again so PATH takes effect.
    pause
    exit /b 0
)
for /f "tokens=*" %%v in ('python --version') do echo   Found %%v

echo.
echo [2/4] Installing Python packages...
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] Package installation failed.
    echo         Check your internet connection and run setup.bat again.
    pause
    exit /b 1
)

echo.
echo [3/4] Installing ffmpeg...
winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo.
    echo [WARNING] ffmpeg install failed. Try manually: winget install Gyan.FFmpeg
)

echo.
echo [4/4] Installing Tesseract OCR...
winget install UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo.
    echo [WARNING] Tesseract install failed. Try manually: winget install UB-Mannheim.TesseractOCR
)

echo.
echo [OK] Installation complete!
echo.
echo For YouTube downloads, log in to YouTube in Firefox.
echo yt-dlp reads cookies directly from Firefox - no manual export needed.
echo.
echo Auto-upload is optional. If you want it, put client_secret.json in
echo config\credentials\ - see the README for how to create it.
echo.
pause
