@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao encontrado. Instale em https://www.python.org/downloads/ marcando "Add Python to PATH".
  pause
  exit /b 1
)
if not exist ".venv" (
  echo Preparando o ambiente pela primeira vez...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python -m ofx2pdf --web
pause
