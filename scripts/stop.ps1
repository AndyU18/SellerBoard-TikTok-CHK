<#
.SYNOPSIS
  Cierra cualquier backend o frontend de CHK Profit que haya quedado abierto y libera sus puertos.

.DESCRIPTION
  Busca procesos de ESTE proyecto (por la ruta de la carpeta en su línea de comandos) que escuchen en los
  puertos de desarrollo (8000-8029 y 5173-5202) y los cierra con todos sus hijos. No toca procesos de otros
  proyectos, aunque usen esos mismos puertos.

.EXAMPLE
  .\scripts\stop.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$ports = @(8000..8029) + @(5173..5202)

function Get-CommandLine([int]$ProcessId) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($p) { $p.CommandLine } else { $null }
}

function Test-ProjectProcess([int]$ProcessId) {
    $line = Get-CommandLine $ProcessId
    return [bool]($line -and $line.ToLower().Contains($root.ToLower()))
}

function Get-ProjectRoot([int]$ProcessId) {
    $current = $ProcessId
    while ($true) {
        $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue).ParentProcessId
        if ($parent -and $parent -ne $current -and (Test-ProjectProcess $parent)) { $current = $parent } else { break }
    }
    return $current
}

$closed = 0
foreach ($c in Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ports -contains $_.LocalPort }) {
    if (Test-ProjectProcess $c.OwningProcess) {
        $rootPid = Get-ProjectRoot $c.OwningProcess
        Write-Host "Cerrando el proceso $rootPid que usaba el puerto $($c.LocalPort)." -ForegroundColor Yellow
        & taskkill.exe /PID $rootPid /T /F 2>&1 | Out-Null
        $closed++
    }
}
if ($closed -eq 0) { Write-Host "No había procesos de CHK Profit abiertos en los puertos de desarrollo." -ForegroundColor Green }
else { Write-Host "Listo: puertos liberados." -ForegroundColor Green }
