<#
.SYNOPSIS
  Prepara los archivos de variables SIN sobrescribir nunca los que ya existen.

.DESCRIPTION
  * backend/.env       <- backend/.env.example     (solo si backend/.env no existe)
  * frontend/.env.local <- frontend/.env.example   (solo si frontend/.env.local no existe)
  * Si el archivo ya existe NO se toca; en su lugar se guarda/actualiza una copia de respaldo `.bak` al lado
    (backend/.env.bak, frontend/.env.local.bak). Están en .gitignore. Si alguna vez un `cp` o un editor
    sobrescribe tu .env, la última copia buena está en el .bak.
  * No imprime ningún valor.

  Úsalo en vez de `cp .env.example .env`. Lo ejecuta solo scripts/dev.ps1 en cada arranque.

.EXAMPLE
  .\scripts\setup-env.ps1
  .\scripts\setup-env.ps1 -Restore    # recupera .env y .env.local desde su .bak (con -Force reemplaza uno que ya tenga valores)
#>
param(
    [switch]$Restore,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$targets = @(
    @{ Name = 'backend/.env';        File = Join-Path $root 'backend\.env';        Template = Join-Path $root 'backend\.env.example' },
    @{ Name = 'frontend/.env.local'; File = Join-Path $root 'frontend\.env.local'; Template = Join-Path $root 'frontend\.env.example' }
)

# "Configurado" = tiene al menos una línea NOMBRE=valor activa (no comentada). Una copia de la plantilla no cuenta:
# así nunca respaldamos encima de una buena copia con una plantilla vacía.
function Test-HasValues([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    return [bool](Select-String -Path $Path -Pattern '^\s*[A-Za-z_][A-Za-z0-9_]*=\S' -Quiet)
}

foreach ($t in $targets) {
    $bak = "$($t.File).bak"
    if ($Restore) {
        if (-not (Test-Path $bak)) { Write-Host "[$($t.Name)] No hay copia de respaldo (.bak) que restaurar." -ForegroundColor Yellow; continue }
        if ((Test-Path $t.File) -and (Test-HasValues $t.File) -and -not $Force) {
            Write-Host "[$($t.Name)] Ya tiene valores; usa -Force si de verdad quieres reemplazarlo con el respaldo." -ForegroundColor Yellow
            continue
        }
        Copy-Item $bak $t.File -Force
        Write-Host "[$($t.Name)] Restaurado desde el respaldo." -ForegroundColor Green
        continue
    }

    if (-not (Test-Path $t.File)) {
        if (Test-Path $t.Template) {
            Copy-Item $t.Template $t.File
            Write-Host "[$($t.Name)] No existía: lo creé desde la plantilla. Llénalo con tus valores." -ForegroundColor Yellow
        } else {
            Write-Host "[$($t.Name)] No existe y tampoco hay plantilla." -ForegroundColor Red
        }
        continue
    }

    if (Test-HasValues $t.File) {
        # Respaldo solo si cambió (así el .bak siempre es la última versión buena y con valores).
        if (-not (Test-Path $bak) -or (Get-FileHash $t.File).Hash -ne (Get-FileHash $bak).Hash) {
            Copy-Item $t.File $bak -Force
        }
        Write-Host "[$($t.Name)] Ya existe: no lo toco (respaldo al día en .bak)." -ForegroundColor Green
    } else {
        Write-Host "[$($t.Name)] Existe pero no tiene valores (¿se sobrescribió con la plantilla?)." -ForegroundColor Red
        if (Test-Path $bak) {
            Write-Host "    Hay una copia buena en el respaldo: .\scripts\setup-env.ps1 -Restore" -ForegroundColor Red
        }
    }
}
