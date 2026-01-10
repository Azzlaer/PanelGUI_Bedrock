@echo off
title LatinBat Bedrock - Instalador de dependencias
echo ==========================================
echo  LatinBat Bedrock Server Manager
echo  Instalando dependencias...
echo ==========================================
echo.

REM --- Verificar Python ---
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo [ERROR] Python no esta instalado o no esta en el PATH
    echo Descarga Python desde https://www.python.org/
    pause
    exit /b
)

REM --- Actualizar pip ---
echo [+] Actualizando pip...
python -m pip install --upgrade pip

REM --- Instalar dependencias ---
echo [+] Instalando requests...
python -m pip install requests

echo [+] Instalando mysql-connector-python...
python -m pip install mysql-connector-python

echo.
echo ==========================================
echo  Dependencias instaladas correctamente
echo ==========================================
pause
