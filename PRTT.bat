@echo off
REM PRTT - doble clic para abrir. Requiere Python 3.8 o superior instalado.
cd /d "%~dp0"
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
if errorlevel 1 pause
