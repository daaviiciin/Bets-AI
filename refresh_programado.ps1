# Bets AI - refresh programado (lo lanza el Programador de tareas de Windows).
# Ejecuta el pipeline diario de Flashscore (calendario, resultados, clasificación,
# stats de equipo/jugador y bajas) con los límites por refresh del CLI.
# NO toca la Odds API (de pago) ni Winamax.
# Log por día en logs\refresh_AAAA-MM-DD.log

$ErrorActionPreference = 'Continue'
# Python escribe UTF-8 (force_utf8_stdio): que PowerShell lo lea igual al capturarlo.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$root = $PSScriptRoot
Set-Location $root
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("refresh_{0:yyyy-MM-dd}.log" -f (Get-Date))

function Write-Log([string]$msg) {
    "{0:HH:mm:ss} | programado | {1}" -f (Get-Date), $msg | Out-File -Append -Encoding utf8 $log
}

Write-Log '=== Inicio del refresh programado ==='

# Si Docker Desktop no está arrancado, se abre y se espera a que el motor responda.
$dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
$dockerTimeoutSec = 300
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    if (-not (Test-Path $dockerDesktop)) {
        Write-Log "ERROR: Docker no responde y no se encuentra $dockerDesktop. Refresh omitido."
        exit 1
    }
    Write-Log 'Docker Desktop no estaba iniciado: arrancándolo...'
    Start-Process -FilePath $dockerDesktop
    $waited = 0
    do {
        Start-Sleep -Seconds 10
        $waited += 10
        docker info *> $null
    } while ($LASTEXITCODE -ne 0 -and $waited -lt $dockerTimeoutSec)
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: Docker no respondió tras $dockerTimeoutSec s. Refresh omitido."
        exit 1
    }
    Write-Log "Docker listo tras $waited s."
}

docker compose up -d *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Log 'ERROR: no se pudo levantar PostgreSQL. Refresh omitido.'
    exit 1
}
# Postgres puede tardar unos segundos en aceptar conexiones tras arrancar el contenedor.
$waited = 0
do {
    $health = docker inspect --format '{{.State.Health.Status}}' betsai_postgres 2>$null
    if ($health -eq 'healthy') { break }
    Start-Sleep -Seconds 5
    $waited += 5
} while ($waited -lt 120)
if ($health -ne 'healthy') {
    Write-Log "ERROR: PostgreSQL no está healthy tras 120 s (estado: $health). Refresh omitido."
    exit 1
}

$python = Join-Path $root '.venv\Scripts\python.exe'
# El logging de Python va a stderr: se convierte a texto para que no salga como error de PowerShell.
& $python -m backend.tools.daily_refresh 2>&1 | ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 $log
$code = $LASTEXITCODE
Write-Log "=== Fin del refresh (código $code) ==="
exit $code
