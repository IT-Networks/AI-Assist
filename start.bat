@echo off
REM ═══════════════════════════════════════════════════════════════════
REM AI Code Assistant - Startskript für Windows
REM ═══════════════════════════════════════════════════════════════════

echo.
echo  AI Code Assistant
echo  ═════════════════
echo.

REM Ins Projektverzeichnis wechseln
cd /d "%~dp0"

REM Prüfen ob venv existiert
if not exist ".venv\Scripts\activate.bat" (
    echo [FEHLER] Virtual Environment nicht gefunden!
    echo.
    echo Bitte erst einrichten:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM venv aktivieren
echo [INFO] Aktiviere Virtual Environment...
call .venv\Scripts\activate.bat

REM Prüfen ob uvicorn installiert ist
python -c "import uvicorn" 2>nul
if errorlevel 1 (
    echo [FEHLER] uvicorn ist nicht installiert!
    echo.
    echo Bitte installieren:
    echo   pip install uvicorn
    echo.
    pause
    exit /b 1
)

REM Server starten
echo [INFO] Starte Server auf http://localhost:8000 ...
echo [INFO] Zum Beenden: Strg+C druecken
echo.

uvicorn main:app --host 0.0.0.0 --port 8000 --reload

REM Falls Server beendet
echo.
echo [INFO] Server beendet.
pause
