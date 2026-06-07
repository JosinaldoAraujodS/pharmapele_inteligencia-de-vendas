@echo off
title Pharmapele - Iniciando Sistema
color 0A

echo.
echo  ██████╗ ██╗  ██╗ █████╗ ██████╗ ███╗   ███╗ █████╗ ██████╗ ███████╗██╗
echo  ██╔══██╗██║  ██║██╔══██╗██╔══██╗████╗ ████║██╔══██╗██╔══██╗██╔════╝██║
echo  ██████╔╝███████║███████║██████╔╝██╔████╔██║███████║██████╔╝█████╗  ██║
echo  ██╔═══╝ ██╔══██║██╔══██║██╔══██╗██║╚██╔╝██║██╔══██║██╔═══╝ ██╔══╝  ██║
echo  ██║     ██║  ██║██║  ██║██║  ██║██║ ╚═╝ ██║██║  ██║██║     ███████╗███████╗
echo  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝╚═╝     ╚══════╝╚══════╝
echo.
echo  Inteligencia de Vendas - Grupo Pharmapele
echo  ==========================================
echo.

cd /d "%~dp0"

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado. Instale em https://python.org
    pause
    exit /b 1
)

:: Instalar dependencias se necessario
if not exist "backend\venv" (
    echo [1/3] Criando ambiente virtual Python...
    python -m venv backend\venv
    echo [2/3] Instalando dependencias...
    backend\venv\Scripts\pip install -r backend\requirements.txt --quiet
    echo [3/3] Dependencias instaladas!
    echo.
)

:: Verificar configuracao do banco
echo Configuracao do Banco de Dados:
echo   Host: %DB_HOST%
if "%DB_HOST%"=="" set DB_HOST=localhost
if "%DB_PORT%"=="" set DB_PORT=5432
if "%DB_NAME%"=="" set DB_NAME=pharmapele
if "%DB_USER%"=="" set DB_USER=postgres
if "%DB_PASS%"=="" set DB_PASS=Ojuara10*

echo   Host: %DB_HOST%
echo   Banco: %DB_NAME%
echo   Usuario: %DB_USER%
echo.

:: Iniciar servidor
echo Iniciando servidor na porta 8000...
echo.
echo  Acesse: http://localhost:8000
echo  (Pressione Ctrl+C para parar)
echo.

cd backend
start http://localhost:8000
..\backend\venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8000 --reload --app-dir .

pause
