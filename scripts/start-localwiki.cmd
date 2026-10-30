@echo off
setlocal EnableExtensions
title LocalWiki
REM Keep window open on failure when double-clicked from Explorer.
REM After code changes: close this window and re-run so new API/UI load.
REM Lock: clear_stale_lock.py --takeover replaces a prior LocalWiki on the same workspace.
cd /d "%~dp0.." || goto :fail
set "ROOT=%CD%"

if not defined LOCALWIKI_WORKSPACE set "LOCALWIKI_WORKSPACE=%ROOT%\workspace"
set "WS=%LOCALWIKI_WORKSPACE%"

set "PY="
if exist "%ROOT%\.venv-work\Scripts\python.exe" set "PY=%ROOT%\.venv-work\Scripts\python.exe"
if not defined PY if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [LocalWiki] python not found. Activate venv or install Python.
    goto :fail
  )
  set "PY=python"
)

echo [LocalWiki] root=%ROOT%
echo [LocalWiki] workspace=%WS%
echo [LocalWiki] python=%PY%

if not exist "%WS%\.kw\config.json" (
  echo [LocalWiki] init workspace...
  "%PY%" -m localwiki --workspace "%WS%" init --json
  if errorlevel 1 goto :fail
)

set "PORT=8765"
if defined LOCALWIKI_PORT set "PORT=%LOCALWIKI_PORT%"
set "PORTFILE=%TEMP%\localwiki_port_%RANDOM%.txt"
"%PY%" -c "import json,sys; from pathlib import Path; p=Path(sys.argv[1])/'.kw'/'config.json'; print(json.load(open(p,encoding='utf-8')).get('port',8765) if p.is_file() else 8765)" "%WS%" >"%PORTFILE%" 2>nul
if exist "%PORTFILE%" (
  set /p PORT=<"%PORTFILE%"
  del /q "%PORTFILE%" >nul 2>&1
)
for /f "delims=0123456789" %%A in ("%PORT%") do set "PORT=8765"
if "%PORT%"=="" set "PORT=8765"

echo [LocalWiki] URL=http://127.0.0.1:%PORT%/
echo [LocalWiki] Starting server - leave this window open.
echo.

REM Ensure work extractors (docx/pdf/xlsx) are present in the chosen venv.
if exist "%ROOT%\requirements-work.txt" (
  "%PY%" -c "import docx,pypdf,openpyxl" >nul 2>&1
  if errorlevel 1 (
    echo [LocalWiki] installing work deps from requirements-work.txt ...
    "%PY%" -m pip install -r "%ROOT%\requirements-work.txt"
    if errorlevel 1 echo [LocalWiki] WARNING: pip install work deps failed - PDF/DOCX/XLSX import may break
  )
)

REM Stale/orphan lock blocks startup. Clear dead holders; take over prior LocalWiki on same workspace
REM (both .kw/lock.json and .kw/runtime/workspace.lock).
echo [LocalWiki] checking workspace lock...
"%PY%" "%ROOT%\scripts\clear_stale_lock.py" "%WS%" --takeover
if errorlevel 1 goto :fail

REM Delay browser open until serve can bind (approx 2s).
start "LocalWiki-browser" /min cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:%PORT%/"

"%PY%" -m localwiki --workspace "%WS%" serve --port %PORT%
set "EC=%ERRORLEVEL%"
echo.
if not "%EC%"=="0" (
  echo [LocalWiki] serve exited with code %EC%
  echo [LocalWiki] If locked: close other LocalWiki windows, or delete "%WS%\.kw\lock.json"
  goto :fail
)
echo [LocalWiki] server stopped.
pause
exit /b 0

:fail
echo.
echo [LocalWiki] FAILED. Read the error above, then press a key to close.
pause
exit /b 1
