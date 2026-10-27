@echo off
setlocal

set "ROOT=%~dp0"
rem Ruta relativa a ROOT (sin espacios): el .venv tiene los mismos paquetes que los tests y la tarea programada.
set "PYTHON=.venv\Scripts\python.exe"
cd /d "%ROOT%"

echo ========================================
echo Bets AI - arranque de desarrollo
echo ========================================
echo.

where docker >nul 2>nul
if errorlevel 1 (
    echo ERROR: Docker no esta instalado o no esta en PATH.
    echo Abre Docker Desktop y vuelve a intentarlo.
    pause
    exit /b 1
)

if not exist "%PYTHON%" (
    echo ERROR: No existe el entorno virtual .venv.
    echo Crealo con: py -3.10 -m venv .venv
    echo y despues:  .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

%PYTHON% -c "import fastapi, uvicorn, sqlalchemy, alembic, limits" >nul 2>nul
if errorlevel 1 (
    echo ERROR: El .venv no tiene instaladas las dependencias del backend.
    echo Instala las dependencias con: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo ERROR: Node.js/npm no esta instalado o no esta en PATH.
    pause
    exit /b 1
)

echo [1/4] Comprobando Docker Desktop...
docker info >nul 2>nul
if errorlevel 1 (
    echo ERROR: Docker Desktop no esta iniciado.
    echo Abre Docker Desktop, espera a que termine de arrancar y ejecuta este script de nuevo.
    pause
    exit /b 1
)

echo [2/4] Levantando PostgreSQL...
docker compose up -d
if errorlevel 1 (
    echo ERROR: No se pudo levantar PostgreSQL.
    pause
    exit /b 1
)

echo [3/4] Aplicando migraciones...
%PYTHON% -m alembic upgrade head
if errorlevel 1 (
    echo ERROR: Fallaron las migraciones Alembic.
    echo Revisa que las dependencias Python esten instaladas y que PostgreSQL este disponible.
    pause
    exit /b 1
)

echo [4/4] Abriendo backend y frontend...
start "Bets AI - Backend" cmd /k "cd /d "%ROOT%" && %PYTHON% -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000"
start "Bets AI - Frontend" cmd /k "cd /d "%ROOT%frontend" && npm start"

echo.
echo Servicios iniciados:
echo   API:     http://127.0.0.1:8000/docs
echo   Frontend: http://localhost:4200
echo.
echo Puedes cerrar esta ventana. No cierres las ventanas de Backend y Frontend.
timeout /t 5 >nul
exit /b 0