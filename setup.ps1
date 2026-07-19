<#
.SYNOPSIS
    FlowGate setup for Windows — the PowerShell counterpart of setup.sh.

.DESCRIPTION
    End to end, mirroring the Linux flow (minus systemd, which Windows lacks):
      - server: python venv (.venv) + dependencies
      - server\.env: working defaults (CONTEXT, DB, SECRET_KEY, token pepper)
      - client: build -> client\dist (same-origin API base)
      - run.bat: a generated launcher (sets FLOWGATE_STORAGE_DIR, starts uvicorn)
      - admin: prompt for username/password and create the first account (sqlite)

    The server supports sqlite3 / mysql / postgres (see server\config.py). The
    default is sqlite3, which needs no external server. Target a networked DB by
    passing -DbType plus the connection parameters:

      .\setup.ps1 -DbType postgres -DbHost 127.0.0.1 -DbPort 5432 `
                  -DbUser flowgate -DbPassword secret -DbDatabase flowgate

    Migrations auto-apply on first boot from sql\migrations\<db>\, so there is no
    manual schema step. Re-runs are safe: SECRET_KEY / token pepper are only
    generated when empty, and the admin account is skipped if it already exists.

.EXAMPLE
    .\setup.ps1
    Sqlite setup with interactive admin bootstrap.

.EXAMPLE
    .\setup.ps1 -DbType mysql -DbHost db.local -DbUser flowgate -DbDatabase flowgate
    MySQL/MariaDB setup (prompts for the password if -DbPassword is omitted).
#>
# Windows PowerShell 5.1 (the Windows default shell) and PowerShell 7.x both work.
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('sqlite3', 'sqlite', 'local', 'mysql', 'postgres')]
    [string]$DbType = 'sqlite3',
    [string]$DbHost,
    [int]$DbPort,
    [string]$DbUser,
    [string]$DbPassword,
    [string]$DbDatabase,
    [string]$DbSchema,
    [int]$Port = 8089,
    [switch]$Start  # start the server in this window after setup
)

$ErrorActionPreference = 'Stop'

$Root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$StorageDir  = Join-Path $Root 'storage'
$EnvFile     = Join-Path $Root 'server\.env'
$EnvSample   = Join-Path $Root 'server\.env.sample'
$Venv        = Join-Path $Root '.venv'
$VenvPython  = Join-Path $Venv 'Scripts\python.exe'

# ── helpers ──────────────────────────────────────────────────────────────────

# Resolve a python launcher: prefer the Windows 'py' launcher, then 'python'.
function Get-BasePython {
    foreach ($cmd in @('py', 'python')) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    throw "Python not found on PATH. Install Python 3 (python.org) and re-run."
}

# Read .env as a list of lines, preserving order; written back without a BOM so
# pydantic / dotenv parse it cleanly (Windows PowerShell utf8 would add a BOM).
function Set-EnvVar([string]$Key, [string]$Value) {
    $lines = @()
    if (Test-Path $EnvFile) { $lines = [System.IO.File]::ReadAllLines($EnvFile) }
    $out = New-Object System.Collections.Generic.List[string]
    $found = $false
    foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            $out.Add("$Key=$Value"); $found = $true
        } else {
            $out.Add($line)
        }
    }
    if (-not $found) { $out.Add("$Key=$Value") }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($EnvFile, $out, $utf8NoBom)
}

function Remove-EnvVar([string]$Key) {
    if (-not (Test-Path $EnvFile)) { return }
    $lines = [System.IO.File]::ReadAllLines($EnvFile) |
        Where-Object { $_ -notmatch "^$([regex]::Escape($Key))=" }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($EnvFile, $lines, $utf8NoBom)
}

function Test-EnvSet([string]$Key) {
    if (-not (Test-Path $EnvFile)) { return $false }
    return [bool](Select-String -Path $EnvFile -Pattern "^$([regex]::Escape($Key))=.+" -Quiet)
}

# Generate a hex secret using the venv python (always present by this point).
function New-Secret { & $VenvPython -c 'import secrets; print(secrets.token_hex(32))' }

# ── server: venv + dependencies ──────────────────────────────────────────────
Write-Host '==> Server: venv + dependencies'
# Remove any stale inner venv (parity with setup.sh: keeps uvicorn's reload
# watcher from crawling the venv tree).
if (Test-Path (Join-Path $Root 'server\.venv')) { Remove-Item -Recurse -Force (Join-Path $Root 'server\.venv') }
if (-not (Test-Path $VenvPython)) {
    $basePython = Get-BasePython
    & $basePython -m venv $Venv
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $Root 'server\requirements.txt')

# ── server: .env ─────────────────────────────────────────────────────────────
Write-Host "==> Server: .env (DB_TYPE=$DbType)"
if (-not (Test-Path $EnvFile)) { Copy-Item $EnvSample $EnvFile }
New-Item -ItemType Directory -Force -Path $StorageDir | Out-Null
Set-EnvVar 'CONTEXT' '/flowgate'
Set-EnvVar 'DB_TYPE' $DbType

switch ($DbType) {
    { $_ -in 'sqlite3', 'sqlite', 'local' } {
        # File-backed DB — no external server needed.
        Set-EnvVar 'DB_PATH' (Join-Path $StorageDir 'flowgate.db')
    }
    { $_ -in 'mysql', 'postgres' } {
        # Networked DB — fill from parameters, prompting for anything left unset.
        if (-not $DbHost)     { $DbHost = Read-Host 'DB host (default 127.0.0.1)'; if (-not $DbHost) { $DbHost = '127.0.0.1' } }
        if (-not $DbPort)     { $def = if ($DbType -eq 'postgres') { 5432 } else { 3306 }; $i = Read-Host "DB port (default $def)"; $DbPort = if ($i) { [int]$i } else { $def } }
        if (-not $DbUser)     { $DbUser = Read-Host 'DB user (default flowgate)'; if (-not $DbUser) { $DbUser = 'flowgate' } }
        if (-not $DbDatabase) { $DbDatabase = Read-Host 'DB name (default flowgate)'; if (-not $DbDatabase) { $DbDatabase = 'flowgate' } }
        if (-not $DbSchema)   { $DbSchema = if ($DbType -eq 'postgres') { 'public' } else { '' } }
        if (-not $DbPassword) {
            $sec = Read-Host 'DB password' -AsSecureString
            $DbPassword = [System.Net.NetworkCredential]::new('', $sec).Password
        }
        Set-EnvVar 'DB_HOST'     $DbHost
        Set-EnvVar 'DB_PORT'     "$DbPort"
        Set-EnvVar 'DB_USER'     $DbUser
        Set-EnvVar 'DB_PASSWORD' $DbPassword
        Set-EnvVar 'DB_DATABASE' $DbDatabase
        Set-EnvVar 'DB_SCHEMA'   $DbSchema
    }
}

# FLOWGATE_STORAGE_DIR is NOT a Settings field (pydantic extra_forbidden). It is
# injected as an OS env var by run.bat instead of living in .env.
Remove-EnvVar 'FLOWGATE_STORAGE_DIR'

# Generate a SECRET_KEY / token pepper only when empty — don't rotate on re-runs.
if (-not (Test-EnvSet 'SECRET_KEY')) { Set-EnvVar 'SECRET_KEY' (New-Secret) }
if (-not (Test-EnvSet 'FLOWGATE_TOKEN_PEPPER_v1')) {
    Set-EnvVar 'FLOWGATE_TOKEN_PEPPER_v1' (New-Secret)
    Set-EnvVar 'FLOWGATE_TOKEN_PEPPER_ACTIVE_ID' 'v1'
}

# ── client: build -> dist ────────────────────────────────────────────────────
Write-Host '==> Client: build -> dist'
$clientDir = Join-Path $Root 'client'
# Highest-precedence Vite env file — overrides the dev client\.env at build time
# so the build uses a same-origin /flowgate base (mirrors client\build.sh).
$apiBase = '/flowgate'
$prodEnv = "# Generated by setup.ps1 - do not edit. Sets the API base for the built client.`nVITE_API_BASE_URL=$apiBase`n"
[System.IO.File]::WriteAllText((Join-Path $clientDir '.env.production.local'), $prodEnv, (New-Object System.Text.UTF8Encoding($false)))
Push-Location $clientDir
try {
    & npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed ($LASTEXITCODE)" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

# ── run.bat launcher (no systemd on Windows) ─────────────────────────────────
Write-Host '==> Launcher: run.bat'
# Sets the storage dir env var and starts uvicorn (no --reload: this is the
# deploy launcher, not the dev watcher). Edit the port here if you change it.
$runBat = @"
@echo off
REM Generated by setup.ps1 - starts the FlowGate server.
setlocal
set "FLOWGATE_STORAGE_DIR=$StorageDir"

REM Build the client so server serves the latest client/dist.
cd /d "%~dp0client"
call npm run build || (echo Client build failed & exit /b 1)

cd /d "%~dp0server"
"$VenvPython" -m uvicorn routers.main:app --host 0.0.0.0 --port $Port
"@
# .bat files MUST use CRLF: cmd.exe seeks by byte offset assuming 2-byte line
# endings, so LF-only files drift and execute mid-line fragments (e.g. a stray
# 'M' from "REM"/"-m"). Normalize regardless of how this .ps1 is saved.
$runBat = $runBat -replace "`r?`n", "`r`n"
[System.IO.File]::WriteAllText((Join-Path $Root 'run.bat'), $runBat, (New-Object System.Text.UTF8Encoding($false)))

# ── admin account (sqlite only) ──────────────────────────────────────────────
Write-Host '==> Admin account'
# create_dev_user.py talks to SQLite directly. For mysql/postgres the server
# still creates the schema on first boot; seed the admin against that DB yourself.
if ($DbType -in 'sqlite3', 'sqlite', 'local') {
    # The schema is created by the server booting and running its migrations, so
    # boot it briefly in the background to seed the admin right away.
    $dbFile = Join-Path $StorageDir 'flowgate.db'
    # Storage dir goes in this scope rather than a Start-Process parameter: the
    # -Environment parameter is PowerShell 7.4+ only and hard-fails on Windows
    # PowerShell 5.1. Child processes inherit it, and create_dev_user.py below
    # needs it too, whether or not we boot the server here.
    $env:FLOWGATE_STORAGE_DIR = $StorageDir
    $readyCheck = Join-Path $Root 'server\check_db_ready.py'
    # Readiness is "every migration committed", NOT "flowgate.db exists": SQLite
    # creates the file the instant sqloader connects, so the old Test-Path probe
    # returned true before 004_rbac.sql had seeded the __SYSTEM__ project and the
    # role rows, and create_dev_user.py then died on a FOREIGN KEY violation.
    & $VenvPython $readyCheck --db $dbFile --quiet
    $dbReady = ($LASTEXITCODE -eq 0)
    if (-not $dbReady) {
        # Covers a first install (no file) and a re-run after this step failed
        # partway, which leaves a file that exists but is only half migrated.
        Write-Host '    Booting the server once to apply DB migrations...'
        $proc = Start-Process -FilePath $VenvPython `
            -ArgumentList @('-m', 'uvicorn', 'routers.main:app', '--host', '127.0.0.1', '--port', "$Port") `
            -WorkingDirectory (Join-Path $Root 'server') `
            -PassThru -WindowStyle Hidden
        # Applying the full migration set takes appreciably longer than the 30s
        # the file-existence probe needed; poll until it actually finishes.
        & $VenvPython $readyCheck --db $dbFile --wait 300
        $dbReady = ($LASTEXITCODE -eq 0)
        # Windows has no clean SIGTERM for a hidden console process, but by here
        # the migrations have either committed or timed out, so nothing that the
        # admin bootstrap depends on is lost by force-stopping.
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    }
    if ($dbReady) {
        $adminUser = Read-Host 'Admin username (default admin)'
        if (-not $adminUser) { $adminUser = 'admin' }
        $adminPw = ''
        while (-not $adminPw) {
            $sec = Read-Host 'Admin password' -AsSecureString
            $adminPw = [System.Net.NetworkCredential]::new('', $sec).Password
        }
        # Skips automatically if the account already exists (re-run safe).
        & $VenvPython (Join-Path $Root 'server\create_dev_user.py') `
            --username $adminUser `
            --email "$adminUser@flowgate.local" `
            --password $adminPw `
            --admin
    } else {
        Write-Host '[!] DB migrations did not finish — create the admin account manually later'
        Write-Host '    (start the server, let it finish migrating, then run):'
        Write-Host "    `"$VenvPython`" server\create_dev_user.py --username admin --email admin@flowgate.local --password <pw> --admin"
    }
} else {
    Write-Host "[i] DB_TYPE=${DbType}: skipping the SQLite admin bootstrap."
    Write-Host "    create_dev_user.py is SQLite-only; seed the first admin directly against your $DbType database."
}

# ── done ─────────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '──────────────────────────────────────────────────────────────'
Write-Host @"

Done. Start FlowGate with:

  run.bat                 (or: .\.venv\Scripts\python -m uvicorn routers.main:app --host 0.0.0.0 --port $Port  from server\)

  Open:     http://localhost:$Port
  DB:       $DbType (schema auto-migrates on first boot from sql\migrations\)
  Storage:  $StorageDir

Notes:
  - Rebuild the client after FE changes:  cd client; npm run build
  - To run as a background Windows service, wrap run.bat with NSSM
    (nssm install FlowGate "$Root\run.bat") or a Task Scheduler "At startup" task.
  - For outbound/external token links, rebuild the client with an absolute base:
      cd client; `$env:VITE_API_BASE_URL='https://<public-host>/flowgate'; npm run build
──────────────────────────────────────────────────────────────
"@

if ($Start) {
    Write-Host ''
    Write-Host '==> Starting FlowGate (Ctrl+C to stop)'
    $env:FLOWGATE_STORAGE_DIR = $StorageDir
    Push-Location (Join-Path $Root 'server')
    try {
        & $VenvPython -m uvicorn routers.main:app --host 0.0.0.0 --port $Port
    } finally {
        Pop-Location
    }
}
