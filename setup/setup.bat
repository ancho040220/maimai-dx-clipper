@echo off
echo ============================================================
echo   maimai DX Rating Clipper - Install
echo ============================================================
echo.

echo [1/3] Installing Python packages...
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed. Make sure Python 3.10+ is installed.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing ffmpeg...
winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo.
    echo [WARNING] ffmpeg install failed. Try manually: winget install Gyan.FFmpeg
)

echo.
echo [3/3] Installing Tesseract OCR...
winget install UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo.
    echo [WARNING] Tesseract install failed. Try manually: winget install UB-Mannheim.TesseractOCR
)

echo.
echo [OK] Installation complete!
echo.
echo Place this file in config\credentials\:
echo   - client_secret.json  (YouTube API credentials, for auto-upload)
echo.
echo For YouTube downloads, log in to YouTube in Firefox.
echo yt-dlp reads cookies directly from Firefox - no manual export needed.
echo.
pause
