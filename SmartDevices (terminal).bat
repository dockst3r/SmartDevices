@echo off
title SmartDevices
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    python -m venv .venv || goto :erro
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :erro
)
".venv\Scripts\python.exe" smart.py
exit /b
:erro
echo Falha ao preparar o ambiente. O Python esta instalado?
pause
