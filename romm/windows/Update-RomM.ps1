<#
.SYNOPSIS
  Updates RomM with RomStop to the newest image and restarts it. Data and settings stay.
#>
[CmdletBinding()]
param([string]$InstallDir = (Join-Path $env:SystemDrive 'RomM'))

# Continue: docker writes progress to stderr, which Windows PowerShell 5.1 would treat as a fatal error. Exit codes are checked instead.
$ErrorActionPreference = 'Continue'
$docker = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
if (-not (Test-Path $docker)) { $docker = 'docker.exe' }
if (-not (Test-Path (Join-Path $InstallDir 'docker-compose.yml'))) { throw "No RomM install found in $InstallDir." }

Push-Location $InstallDir
try {
    $image = (Select-String -Path .env -Pattern '^ROMM_IMAGE=(.*)$').Matches[0].Groups[1].Value
    # Installs from before the rename use the old image names; move them to RomStop's.
    $renamed = @{ 'ghcr.io/itsdinkd/romshelf-romm:latest' = 'ghcr.io/itsdinkd/romstop-romm:latest'; 'romshelf-romm:local' = 'romstop-romm:local' }
    if ($renamed.ContainsKey($image)) {
        $image = $renamed[$image]
        (Get-Content .env -Raw) -replace '(?m)^ROMM_IMAGE=.*$', "ROMM_IMAGE=$image" | Set-Content .env -NoNewline
    }
    if ($image -eq 'romstop-romm:local') {
        Write-Host 'Rebuilding RomM with RomStop from the repo...'
        & $docker build --pull -t romstop-romm:local 'https://github.com/itsdinkd/RomStop.git#main:romm'
        if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
        & $docker compose pull romm-db
    } else {
        Write-Host "Downloading $image..."
        & $docker compose pull
        if ($LASTEXITCODE -ne 0) { throw 'Download failed.' }
    }
    & $docker compose up -d --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw 'Restart failed.' }
    & $docker image prune -f | Out-Null
    Write-Host 'RomM is up to date.'
} finally {
    Pop-Location
}
