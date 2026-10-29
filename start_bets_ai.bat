@echo off
setlocal

set "ROOT=%~dp0"
rem Ruta relativa a ROOT (sin espacios): el .venv tiene los mismos paquetes que los tests y la tarea programada.
set "PYTHON=.venv\Scripts\python.exe"
set "PY_VERSION=3.13"
set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
cd /d "%ROOT%"

echo ========================================
echo Bets AI - arranque de desarrollo
echo ========================================
echo.

where docker >nul 2>nul
if errorlevel 1 (
    echo ERROR: Docker no esta instalado o no esta en PATH.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo ERROR: Node.js/npm no esta instalado o no esta en PATH.
    pause
    exit /b 1
)

if not exist ".env" (
    echo ERROR: Falta el fichero .env.
    echo Copia .env.example a .env y rellena POSTGRES_PASSWORD, DATABASE_URL y JWT_SECRET.
    pause
    exit /b 1
)

rem Primer arranque en un equipo nuevo: se crea el .venv con Python %PY_VERSION%.
if not exist "%PYTHON%" (
    echo Creando el entorno virtual .venv con Python %PY_VERSION%...
    py -%PY_VERSION% -m venv .venv
    if errorlevel 1 (
        echo ERROR: No se pudo crear el .venv. Instala Python %PY_VERSION% ^(con el lanzador py^).
        pause
        exit /b 1
    )
)

%PYTHON% -c "import fastapi, uvicorn, sqlalchemy, alembic, limits, tzdata" >nul 2>nul
if errorlevel 1 (
    echo Instalando dependencias del backend...
    %PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Fallo pip install -r requirements.txt.
        pause
        exit /b 1
    )
)

if not exist "frontend\node_modules" (
    echo Instalando dependencias del frontend...
    pushd frontend
    call npm ci
    if errorlevel 1 (
        popd
        echo ERROR: Fallo npm ci en frontend.
        pause
        exit /b 1
    )
    popd
)

echo [1/4] Comprobando Docker Desktop...
docker info >nul 2>nul
if not errorlevel 1 goto docker_ok
if not exist "%DOCKER_DESKTOP%" (
    echo ERROR: Docker Desktop no esta iniciado y no se encuentra en "%DOCKER_DESKTOP%".
    pause
    exit /b 1
)
echo Docker Desktop no estaba iniciado: arrancandolo ^(hasta 3 min^)...
start "" "%DOCKER_DESKTOP%"
set /a DOCKER_WAIT=0
:docker_wait
timeout /t 5 /nobreak >nul
set /a DOCKER_WAIT+=5
docker info >nul 2>nul
if not errorlevel 1 goto docker_ok
if %DOCKER_WAIT% lss 180 goto docker_wait
echo ERROR: Docker Desktop no respondio a tiempo.
pause
exit /b 1
:docker_ok

echo [2/4] Levantando PostgreSQL...
rem --wait espera al healthcheck (pg_isready): sin el, las migraciones fallan en un arranque en frio.
docker compose up -d --wait
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
