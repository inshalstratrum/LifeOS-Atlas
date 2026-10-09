@echo off
setlocal
set "ATLAS_ROOT=%~dp0"
cd /d "%ATLAS_ROOT%"

echo ========================================================
echo   LifeOS Atlas - Semantic Deduplication & Diff Engine
echo ========================================================

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Starting LifeOS Atlas on 127.0.0.1...
    python app\server.py --open --audit
    goto :end
)

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Starting LifeOS Atlas with Python launcher...
    py -3 app\server.py --open --audit
    goto :end
)

echo [ERROR] Python was not found on PATH. Please ensure Python 3.10+ is installed.

:end
echo.
pause
endlocal
