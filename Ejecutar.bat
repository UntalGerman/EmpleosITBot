@echo off
chcp 65001 > nul
title Agente de Busqueda de Empleo
color 0B
cls

echo.
echo  ============================================================
echo   AGENTE DE BUSQUEDA DE EMPLEO  v3.3
echo   Powered by Playwright + Claude Haiku
echo  ============================================================
echo.
echo   Portales  : Computrabajo / EmpleosIT / Indeed / LinkedIn
echo             : Workana / Bumeran
echo   Modelo IA : Claude Haiku (triage + analisis profundo)
echo   Extras    : SQLite + Alertas Telegram + scraping paralelo
echo.
echo  ------------------------------------------------------------
echo.

python --version > nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo  [ERROR] Python no esta instalado o no esta en el PATH.
    echo  Descargalo desde https://www.python.org/downloads/
    echo.
    pause
    exit /b
)

if not exist "%~dp0main.py" (
    color 0C
    echo  [ERROR] No se encontro main.py en esta carpeta.
    echo  Asegurate de que todos los archivos esten juntos.
    echo.
    pause
    exit /b
)

if not exist "%~dp0.env" (
    color 0E
    echo  [AVISO] No se encontro el archivo .env con la API Key.
    echo  Crea un archivo .env con:  ANTHROPIC_API_KEY=sk-ant-...
    echo.
    pause
    exit /b
)

cd /d "%~dp0"
python main.py

echo.
echo  ------------------------------------------------------------
echo   Sesion finalizada. Presiona cualquier tecla para cerrar.
echo  ------------------------------------------------------------
echo.
pause > nul
