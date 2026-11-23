<#
.SYNOPSIS
  Arranca backend y frontend de CHK Profit y los cierra por completo al salir (Ctrl+C o cerrar la ventana).

.DESCRIPTION
  * Antes de arrancar, si un backend/frontend ANTERIOR DE ESTE PROYECTO sigue ocupando el puerto, lo cierra.
  * Si el puerto lo usa OTRO programa (otro proyecto, un puerto que Windows reservó), no lo toca: usa el
    siguiente puerto libre, así dos proyectos nunca chocan.
  * Al salir cierra los dos procesos y todos sus hijos (uvicorn --reload deja procesos hijos), y los puertos
    quedan libres.

.EXAMPLE
  .\scripts\dev.ps1                      # puertos 8000 (API) y 5173 (interfaz), o los libres siguientes
  .\scripts\dev.ps1 -NoSync              # sin la sincronización automática con las plataformas
  .\scripts\dev.ps1 -BackendPort 9000    # empezar a buscar desde otro puerto
#>
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$NoSync,
    [int]$RunFor = 0   # solo para pruebas: salir solo tras N segundos (pasa por la misma limpieza)
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Crea los .env que falten (nunca sobrescribe uno existente) y mantiene su respaldo .bak al día.
& (Join-Path $PSScriptRoot 'setup-env.ps1')

function Get-CommandLine([int]$ProcessId) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($p) { $p.CommandLine } else { $null }
}

function Test-ProjectProcess([int]$ProcessId) {
    $line = Get-CommandLine $ProcessId
    return [bool]($line -and $line.ToLower().Contains($root.ToLower()))
}

# El proceso "raíz" de este proyecto: sube por los padres mientras también sean de este proyecto, para cerrar
# el supervisor de `uvicorn --reload` y no solo el hijo que tiene el puerto.
function Get-ProjectRoot([int]$ProcessId) {
    $current = $ProcessId
    while ($true) {
        $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue).ParentProcessId
        if ($parent -and $parent -ne $current -and (Test-ProjectProcess $parent)) { $current = $parent } else { break }
    }
    return $current
}

function Stop-Tree([int]$ProcessId) {
    & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
}

function Get-PortOwner([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $c.OwningProcess } else { $null }
}

# ¿Se puede abrir el puerto? Detecta también los puertos que Windows reserva (error 10013) aunque nadie los use.
function Test-PortBindable([int]$Port) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

function Resolve-Port([int]$Start, [string]$Label) {
    for ($port = $Start; $port -lt $Start + 30; $port++) {
        $owner = Get-PortOwner $port
        if ($owner -and (Test-ProjectProcess $owner)) {
            $rootPid = Get-ProjectRoot $owner
            Write-Host "[$Label] El puerto $port lo ocupa una copia anterior de este proyecto (PID $rootPid): la cierro." -ForegroundColor Yellow
            Stop-Tree $rootPid
            Start-Sleep -Milliseconds 800
        }
        if (Test-PortBindable $port) { return $port }
        Write-Host "[$Label] El puerto $port no está disponible (otro programa o reservado por Windows): pruebo el siguiente." -ForegroundColor DarkYellow
    }
    throw "No encontré un puerto libre para $Label entre $Start y $($Start + 29)."
}

$be = Resolve-Port $BackendPort 'API'
$fe = Resolve-Port $FrontendPort 'Interfaz'

# El proxy de Vite lee el puerto del backend de esta variable (ver frontend/vite.config.ts).
$env:CHK_BACKEND_PORT = "$be"
if ($NoSync) { $env:CHK_AUTO_SYNC = 'false' }

$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$backend = $null
$frontend = $null

try {
    Write-Host ""
    Write-Host "  API:       http://127.0.0.1:$be   (documentación: /docs)" -ForegroundColor Green
    Write-Host "  Interfaz:  http://localhost:$fe" -ForegroundColor Green
    Write-Host "  Ctrl+C para detener todo y liberar los puertos." -ForegroundColor Gray
    Write-Host ""

    $backend = Start-Process -FilePath 'uv' -ArgumentList @('run', 'uvicorn', 'app.main:app', '--reload', '--host', '127.0.0.1', '--port', "$be") `
        -WorkingDirectory $backendDir -NoNewWindow -PassThru
    $frontend = Start-Process -FilePath 'npm.cmd' -ArgumentList @('run', 'dev', '--', '--port', "$fe", '--strictPort') `
        -WorkingDirectory $frontendDir -NoNewWindow -PassThru

    $started = Get-Date
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
        if ($RunFor -gt 0 -and ((Get-Date) - $started).TotalSeconds -ge $RunFor) { break }
    }
    if ($backend.HasExited) { Write-Host "El backend terminó (código $($backend.ExitCode))." -ForegroundColor Red }
    if ($frontend.HasExited) { Write-Host "El frontend terminó (código $($frontend.ExitCode))." -ForegroundColor Red }
}
finally {
    Write-Host ""
    Write-Host "Cerrando backend y frontend..." -ForegroundColor Yellow
    foreach ($p in @($backend, $frontend)) {
        if ($p -and -not $p.HasExited) { Stop-Tree $p.Id }
    }
    # Por si algún hijo quedó escuchando: cierra solo lo que sea de este proyecto en los dos puertos usados.
    foreach ($port in @($be, $fe)) {
        $owner = Get-PortOwner $port
        if ($owner -and (Test-ProjectProcess $owner)) { Stop-Tree (Get-ProjectRoot $owner) }
    }
    Start-Sleep -Milliseconds 500
    foreach ($port in @($be, $fe)) {
        if (Get-PortOwner $port) { Write-Host "  Aviso: el puerto $port sigue ocupado." -ForegroundColor Red }
        else { Write-Host "  Puerto $port liberado." -ForegroundColor Green }
    }
}
