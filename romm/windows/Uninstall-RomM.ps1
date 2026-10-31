<#
.SYNOPSIS
  Stops and removes the RomM containers. Your ROM folder is never touched.

.PARAMETER RemoveData
  Also deletes RomM's database, artwork cache and settings. Docker Desktop and WSL stay installed.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:SystemDrive 'RomM'),
    [switch]$RemoveData
)

$ErrorActionPreference = 'Continue'
$docker = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
if (-not (Test-Path $docker)) { $docker = 'docker.exe' }

if (Test-Path (Join-Path $InstallDir 'docker-compose.yml')) {
    Push-Location $InstallDir
    if ($RemoveData) { & $docker compose down --volumes --rmi all } else { & $docker compose down }
    Pop-Location
}
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'RomM.url')
Remove-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'RomMRomshelfSetup' -ErrorAction SilentlyContinue
if ($RemoveData) {
    foreach ($f in '.env', 'docker-compose.yml', 'installer-settings.json', 'install.log', 'config', 'assets') {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $InstallDir $f)
    }
}
Write-Host 'RomM is removed. Your ROM library was left as it is.'
exit 0
