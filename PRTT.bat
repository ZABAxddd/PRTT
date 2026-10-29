@echo off
REM PRTT - doble clic para abrir. Requiere Python 3.8 o superior instalado.
cd /d "%~dp0"
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
    "%VIRTUAL_ENV%\Scripts\python.exe" prtt.py %*
    goto end
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" prtt.py %*
    goto end
)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 prtt.py %*
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python prtt.py %*
    ) else (
        echo No se encontro Python. Instala Python 3 desde https://www.python.org/downloads/
        echo y marca la casilla "Add python.exe to PATH" durante la instalacion.
    )
)
:end
if errorlevel 1 pause
