@echo off
setlocal
title Conversor OFX para PDF
cd /d "%~dp0"
set "PYTHONUTF8=1"

rem ------------------------------------------------------------------
rem  Conversor OFX -> PDF
rem  De dois cliques neste arquivo. Na primeira vez ele prepara o
rem  ambiente (precisa de internet); depois abre direto no navegador.
rem ------------------------------------------------------------------

rem 1) Localiza o Python (lancador "py" ou "python")
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (python --version >nul 2>&1 && set "PY=python")
if not defined PY goto :sem_python

rem 2) Cria o ambiente isolado na pasta .venv (somente na primeira vez)
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" goto :dependencias
echo Preparando o conversor pela primeira vez. Isso pode levar alguns minutos...
%PY% -m venv "%~dp0.venv"
if errorlevel 1 goto :erro

:dependencias
rem 3) Instala/atualiza os componentes quando requirements.txt mudar
fc /b "requirements.txt" "%~dp0.venv\requirements.instalado" >nul 2>&1
if not errorlevel 1 goto :iniciar
echo Instalando componentes necessarios...
"%VPY%" -m pip install --disable-pip-version-check --quiet -r requirements.txt
if errorlevel 1 goto :erro
copy /y "requirements.txt" "%~dp0.venv\requirements.instalado" >nul

:iniciar
rem 4) Abre a interface (somente neste computador: http://127.0.0.1:5000)
echo.
"%VPY%" -m ofx2pdf --web %*
if errorlevel 1 pause
exit /b 0

:sem_python
echo.
echo  O Python nao foi encontrado neste computador.
echo.
echo   1. Baixe o instalador na pagina que sera aberta agora.
echo   2. Na primeira tela da instalacao, MARQUE a opcao "Add Python to PATH".
echo   3. Depois de instalar, abra este arquivo novamente.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:erro
echo.
echo  Nao foi possivel preparar o conversor.
echo  Verifique a conexao com a internet e tente novamente.
echo  Se o problema continuar, apague a pasta ".venv" ao lado deste arquivo e abra de novo.
echo.
pause
exit /b 1
