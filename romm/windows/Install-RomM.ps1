<#
.SYNOPSIS
  Installs RomM with the RomStop integration on Windows 11.

.DESCRIPTION
  Sets up WSL2 and Docker Desktop when they are missing, writes a docker-compose
  project to the install folder and starts RomM (with RomStop) and its MariaDB
  database. Run again at any time: it keeps the existing passwords and data, so it
  doubles as a repair and an upgrade.

  Exit codes: 0 done, 3010 restart Windows and the installer continues by itself,
  anything else failed (see install.log in the install folder).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File Install-RomM.ps1 -LibraryDir D:\ROMs
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:SystemDrive 'RomM'),
    [string]$LibraryDir = '',
    [int]$Port = 8080,
    [string]$Image = 'ghcr.io/itsdinkd/romstop-romm:latest',
    # ROMs RomM scans at once. 0 keeps the value already in .env, or 8 on a new install.
    # Set it to your ScreenScraper thread count; RomM never goes past that many ScreenScraper requests anyway.
    [int]$ScanWorkers = 0,
    # Optional JSON file with LibraryDir, Port and metadata keys; deleted after reading.
    [string]$SettingsFile = '',
    [switch]$Resume,
    [switch]$NoBrowser,
    # Write the compose project only; skip WSL, Docker and starting containers.
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$SourceRepo = 'https://github.com/itsdinkd/RomStop.git#main:romm'
$DockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
$DockerDesktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
$DockerInstallerUrl = 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'
$RebootExitCode = 3010
$MetadataKeys = @('SCREENSCRAPER_USER', 'SCREENSCRAPER_PASSWORD', 'RETROACHIEVEMENTS_API_KEY', 'STEAMGRIDDB_API_KEY', 'IGDB_CLIENT_ID', 'IGDB_CLIENT_SECRET')

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$LogFile = Join-Path $InstallDir 'install.log'
$StateFile = Join-Path $InstallDir 'installer-settings.json'
$EnvFile = Join-Path $InstallDir '.env'
$ComposeFile = Join-Path $InstallDir 'docker-compose.yml'

function Write-Step([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line -ForegroundColor Cyan
    Add-Content -Path $LogFile -Value $line
}

function Write-Note([string]$Message) {
    Write-Host "    $Message"
    Add-Content -Path $LogFile -Value "    $Message"
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

function New-Secret {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

function Read-EnvFile([string]$Path) {
    $values = [ordered]@{}
    if (Test-Path $Path) {
        foreach ($line in Get-Content $Path) {
            if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
        }
    }
    return $values
}

function Invoke-Native([scriptblock]$Command, [switch]$Quiet) {
    # Windows PowerShell 5.1 turns anything a program writes to stderr into an error, and with
    # ErrorActionPreference = Stop that ends the script even when the program succeeded (docker and
    # winget print progress there). Run programs with Continue and judge them by $LASTEXITCODE.
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command 2>&1 | ForEach-Object { if (-not $Quiet) { Add-Content -Path $LogFile -Value "$_" } }
    } finally {
        $ErrorActionPreference = $saved
    }
}

function Test-IsAdmin {
    if ($DryRun) { return $true }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Save-State {
    $state = [ordered]@{ InstallDir = $InstallDir; LibraryDir = $LibraryDir; Port = $Port; Image = $Image }
    Write-Utf8NoBom $StateFile ($state | ConvertTo-Json)
}

function Register-Resume {
    # Picks the install up again after the restart that WSL needs.
    $cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -Resume -InstallDir "{1}"' -f $PSCommandPath, $InstallDir
    New-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'RomMRomshelfSetup' -Value $cmd -PropertyType String -Force | Out-Null
}

function Get-FeatureState([string]$Name) {
    try { return (Get-WindowsOptionalFeature -Online -FeatureName $Name).State.ToString() } catch { return 'Unknown' }
}

function Install-Wsl {
    Write-Step 'Checking WSL2'
    $wslOn = (Get-FeatureState 'Microsoft-Windows-Subsystem-Linux') -eq 'Enabled'
    $vmOn = (Get-FeatureState 'VirtualMachinePlatform') -eq 'Enabled'
    if ($wslOn -and $vmOn) {
        Write-Note 'WSL2 is on.'
        Invoke-Native { & wsl.exe --update } -Quiet
        Invoke-Native { & wsl.exe --set-default-version 2 } -Quiet
        return $false
    }
    Write-Note 'Turning on WSL2 and the Virtual Machine Platform (Docker Desktop runs on them).'
    Invoke-Native { & wsl.exe --install --no-distribution }
    if ($LASTEXITCODE -ne 0) {
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart -All | Out-Null
        Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart -All | Out-Null
    }
    return $true
}

function Find-Docker {
    if (Test-Path $DockerExe) { return $DockerExe }
    $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Install-DockerDesktop {
    Write-Step 'Checking Docker Desktop'
    if (Find-Docker) { Write-Note 'Docker Desktop is installed.'; return }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Note 'Installing Docker Desktop with winget (a few minutes)...'
        Invoke-Native { & winget.exe install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements --silent --override 'install --quiet --accept-license --backend=wsl-2' }
    }
    if (-not (Find-Docker)) {
        Write-Note 'Downloading the Docker Desktop installer...'
        $setup = Join-Path $env:TEMP 'DockerDesktopInstaller.exe'
        Invoke-WebRequest -Uri $DockerInstallerUrl -OutFile $setup -UseBasicParsing
        Write-Note 'Installing Docker Desktop (a few minutes)...'
        $p = Start-Process -FilePath $setup -ArgumentList 'install', '--quiet', '--accept-license', '--backend=wsl-2' -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "Docker Desktop's installer failed with exit code $($p.ExitCode)." }
    }
    if (-not (Find-Docker)) { throw 'Docker Desktop did not install. See install.log.' }
    # Docker Desktop only lets members of docker-users talk to it.
    $user = "$env:USERDOMAIN\$env:USERNAME"
    # Fails harmlessly (system error 1378) when the user is already a member.
    Invoke-Native { & net.exe localgroup docker-users $user /add } -Quiet
}

function Start-DockerDesktop {
    Write-Step 'Starting Docker Desktop'
    $docker = Find-Docker
    Invoke-Native { & $docker info } -Quiet
    if ($LASTEXITCODE -eq 0) { Write-Note 'Docker is running.'; return $docker }
    if (Test-Path $DockerDesktopExe) { Start-Process -FilePath $DockerDesktopExe | Out-Null }
    $deadline = (Get-Date).AddMinutes(6)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        Invoke-Native { & $docker info } -Quiet
        if ($LASTEXITCODE -eq 0) { Write-Note 'Docker is running.'; return $docker }
    }
    throw 'Docker Desktop did not start within 6 minutes. Open Docker Desktop once, accept its terms, then run this installer again.'
}

function ConvertTo-ComposePath([string]$Path) {
    return ([System.IO.Path]::GetFullPath($Path)).Replace('\', '/')
}

function Write-ComposeProject([hashtable]$Keys) {
    Write-Step 'Writing the RomM setup'
    $existing = Read-EnvFile $EnvFile
    $envValues = [ordered]@{
        ROMM_IMAGE            = $Image
        ROMM_PORT             = "$Port"
        ROMM_LIBRARY          = (ConvertTo-ComposePath $LibraryDir)
        DB_PASSWD             = $(if ($existing['DB_PASSWD']) { $existing['DB_PASSWD'] } else { New-Secret })
        DB_ROOT_PASSWD        = $(if ($existing['DB_ROOT_PASSWD']) { $existing['DB_ROOT_PASSWD'] } else { New-Secret })
        ROMM_AUTH_SECRET_KEY  = $(if ($existing['ROMM_AUTH_SECRET_KEY']) { $existing['ROMM_AUTH_SECRET_KEY'] } else { New-Secret })
        SCAN_WORKERS          = $(if ($ScanWorkers -gt 0) { "$ScanWorkers" } elseif ($existing['SCAN_WORKERS']) { $existing['SCAN_WORKERS'] } else { '8' })
    }
    foreach ($k in $MetadataKeys) {
        $v = $Keys[$k]
        if (-not $v) { $v = $existing[$k] }
        $envValues[$k] = $(if ($v) { $v } else { '' })
    }
    $lines = @('# Written by the RomM + RomStop installer. Passwords were generated for this PC; keep this file private.')
    foreach ($k in $envValues.Keys) { $lines += "$k=$($envValues[$k])" }
    Write-Utf8NoBom $EnvFile (($lines -join "`n") + "`n")

    $compose = @'
# RomM with the RomStop integration. Settings live in .env next to this file.
name: romm
services:
  romm:
    image: ${ROMM_IMAGE}
    container_name: romm
    restart: unless-stopped
    environment:
      - DB_HOST=romm-db
      - DB_NAME=romm
      - DB_USER=romm-user
      - DB_PASSWD=${DB_PASSWD}
      - ROMM_AUTH_SECRET_KEY=${ROMM_AUTH_SECRET_KEY}
      - SCREENSCRAPER_USER=${SCREENSCRAPER_USER}
      - SCREENSCRAPER_PASSWORD=${SCREENSCRAPER_PASSWORD}
      - RETROACHIEVEMENTS_API_KEY=${RETROACHIEVEMENTS_API_KEY}
      - STEAMGRIDDB_API_KEY=${STEAMGRIDDB_API_KEY}
      - IGDB_CLIENT_ID=${IGDB_CLIENT_ID}
      - IGDB_CLIENT_SECRET=${IGDB_CLIENT_SECRET}
      - HASHEOUS_API_ENABLED=true
      # RomM's default of 4 leaves most ScreenScraper threads idle; RomM caps ScreenScraper itself at the account's thread limit.
      - SCAN_WORKERS=${SCAN_WORKERS:-8}
    volumes:
      - romm_resources:/romm/resources
      - romm_redis_data:/redis-data
      - ${ROMM_LIBRARY}:/romm/library
      - ./assets:/romm/assets
      - ./config:/romm/config
    ports:
      - ${ROMM_PORT}:8080
    depends_on:
      romm-db:
        condition: service_healthy
        restart: true

  romm-db:
    image: mariadb:11
    container_name: romm-db
    restart: unless-stopped
    environment:
      - MARIADB_ROOT_PASSWORD=${DB_ROOT_PASSWD}
      - MARIADB_DATABASE=romm
      - MARIADB_USER=romm-user
      - MARIADB_PASSWORD=${DB_PASSWD}
    volumes:
      - mysql_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      start_period: 30s
      start_interval: 10s
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  mysql_data:
  romm_resources:
  romm_redis_data:
'@
    Write-Utf8NoBom $ComposeFile $compose
    foreach ($d in 'assets', 'config') { New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $d) | Out-Null }
    $configYml = Join-Path $InstallDir 'config\config.yml'
    if (-not (Test-Path $configYml)) { Write-Utf8NoBom $configYml "# RomM settings: https://docs.romm.app/latest/Getting-Started/Configuration-File/`n" }
    Write-Note "Saved $ComposeFile and .env"
}

function Initialize-Library {
    New-Item -ItemType Directory -Force -Path $LibraryDir | Out-Null
    $hasContent = Get-ChildItem -Path $LibraryDir -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $hasContent) {
        # RomM's default layout: <library>\roms\<platform>\<game files>
        foreach ($p in 'nes', 'snes', 'gb', 'gbc', 'gba', 'n64', 'genesis', 'psx') {
            New-Item -ItemType Directory -Force -Path (Join-Path $LibraryDir "roms\$p") | Out-Null
        }
        Write-Note "Created $LibraryDir\roms\<platform> folders. Put your ROMs there, then run a scan in RomM."
    }
}

function Start-RomM([string]$Docker) {
    Write-Step 'Downloading RomM with RomStop'
    Push-Location $InstallDir
    try {
        Invoke-Native { & $Docker compose pull }
        if ($LASTEXITCODE -ne 0) {
            Write-Note 'The prebuilt image is not available; building it from the RomStop repo instead (10 to 20 minutes, once)...'
            Invoke-Native { & $Docker build -t romstop-romm:local $SourceRepo }
            if ($LASTEXITCODE -ne 0) { throw 'Building the RomM image failed. See install.log.' }
            $script:Image = 'romstop-romm:local'
            $envText = (Get-Content $EnvFile -Raw) -replace '(?m)^ROMM_IMAGE=.*$', "ROMM_IMAGE=$Image"
            Write-Utf8NoBom $EnvFile $envText
            Invoke-Native { & $Docker compose pull romm-db }
        }
        Write-Step 'Starting RomM'
        Invoke-Native { & $Docker compose up -d --remove-orphans }
        if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed. See install.log.' }
    } finally {
        Pop-Location
    }
}

function Wait-RomM {
    Write-Step "Waiting for RomM on http://localhost:$Port"
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$Port/api/heartbeat" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { Write-Note 'RomM is up.'; return }
        } catch { Start-Sleep -Seconds 5 }
    }
    throw "RomM did not answer on port $Port within 5 minutes. Check 'docker logs romm'."
}

function New-Shortcuts {
    $url = "http://localhost:$Port"
    $desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
    Write-Utf8NoBom (Join-Path $desktop 'RomM.url') "[InternetShortcut]`r`nURL=$url`r`n"
}

# ---------------------------------------------------------------------------

try {
    if (-not (Test-IsAdmin)) {
        Write-Host 'Restarting as administrator...'
        $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") + ($PSBoundParameters.GetEnumerator() | ForEach-Object {
                if ($_.Value -is [switch]) { if ($_.Value) { "-$($_.Key)" } } else { "-$($_.Key)", "`"$($_.Value)`"" }
            })
        $p = Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -Wait -PassThru
        exit $p.ExitCode
    }

    $keys = @{}
    # A re-run (repair, upgrade, or the resume after a restart) keeps the earlier choices unless they are given again.
    if (Test-Path $StateFile) {
        $state = Get-Content $StateFile -Raw | ConvertFrom-Json
        if (-not $PSBoundParameters.ContainsKey('LibraryDir') -and $state.LibraryDir) { $LibraryDir = $state.LibraryDir }
        if (-not $PSBoundParameters.ContainsKey('Port') -and $state.Port) { $Port = [int]$state.Port }
        if (-not $PSBoundParameters.ContainsKey('Image') -and $state.Image) { $Image = $state.Image }
    }
    if ($Resume) { Write-Step 'Continuing the RomM install after the restart' }
    if ($SettingsFile -and (Test-Path $SettingsFile)) {
        $s = Get-Content $SettingsFile -Raw | ConvertFrom-Json
        if ($s.LibraryDir) { $LibraryDir = $s.LibraryDir }
        if ($s.Port) { $Port = [int]$s.Port }
        foreach ($k in $MetadataKeys) { if ($s.$k) { $keys[$k] = [string]$s.$k } }
        Remove-Item $SettingsFile -Force
    }
    if (-not $LibraryDir) { $LibraryDir = Join-Path $InstallDir 'library' }
    Save-State

    Write-Step "RomM + RomStop installer (install folder $InstallDir, library $LibraryDir, port $Port)"
    $build = [Environment]::OSVersion.Version.Build
    if (-not $DryRun -and $build -lt 22000) { Write-Note "This is Windows build $build. It is made for Windows 11, but Windows 10 22H2 also works." }

    Initialize-Library
    Write-ComposeProject $keys
    if ($DryRun) { Write-Step 'Dry run: compose project written, nothing started.'; exit 0 }

    if (Install-Wsl) {
        Register-Resume
        Write-Step 'Windows needs to restart to finish turning on WSL2. The installer continues by itself after you sign in again.'
        exit $RebootExitCode
    }
    Install-DockerDesktop
    $docker = Start-DockerDesktop
    Start-RomM $docker
    Wait-RomM
    New-Shortcuts
    Write-Step "Done. Open http://localhost:$Port to create your RomM admin account, then scan your library."
    Write-Note 'RomStop is in the top bar, and on every game page under the RomStop tab.'
    if (-not $NoBrowser) { Start-Process "http://localhost:$Port" }
    if ($Resume) { Read-Host 'Press Enter to close' | Out-Null }
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Add-Content -Path $LogFile -Value "ERROR: $($_.Exception.Message)`n$($_.ScriptStackTrace)"
    if ($Resume) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}
