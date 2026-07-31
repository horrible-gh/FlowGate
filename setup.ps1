<#
.SYNOPSIS
    FlowGate setup for Windows — the PowerShell counterpart of setup.sh.

.DESCRIPTION
    End to end, mirroring the Linux flow (minus systemd, which Windows lacks):
      - server: python venv (.venv) + dependencies
      - server\.env: working defaults (CONTEXT, DB, listen address, CORS origin,
        SECRET_KEY, token pepper, TOTP + git encryption keys)
      - client: build -> client\dist (same-origin API base)
      - run.bat: a generated launcher (sets FLOWGATE_STORAGE_DIR, starts uvicorn)
      - admin: create the first account, on any supported engine
      - AI provider: offer to register the first one (server\seed_ai_provider.py);
        setting any $env:FLOWGATE_AI_* seeds it without the prompt

    Declining that last step is fine — .\setup-ai.ps1 runs it on its own afterwards,
    and is also how you add a second provider to the fallback chain later.

    The server supports sqlite3 / mysql / postgres (see server\config.py). The
    engine is asked for when -DbType is omitted; the default is sqlite3, which
    needs no external server. Target a networked DB by passing -DbType plus the
    connection parameters:

      .\setup.ps1 -DbType postgres -DbHost 127.0.0.1 -DbPort 5432 `
                  -DbUser flowgate -DbPassword secret -DbDatabase flowgate

    Migrations auto-apply on first boot from sql\migrations\<db>\, so there is no
    manual schema step. Re-runs are safe: SECRET_KEY / token pepper / encryption
    keys are only generated when empty, and the admin account is skipped if it
    already exists.

    An unattended run supplies the answers up front, and never prompts:

      .\setup.ps1 -DbType postgres -DbHost db -DbUser flowgate -DbPassword secret `
                  -DbDatabase flowgate -AllowedOrigin https://flowgate.example.com `
                  -AdminUsername admin -AdminPassword secret

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
    [string]$BindHost = '0.0.0.0',
    # CORS origin. Left empty the installer asks, and falls back to '*' with a
    # warning (0273 NR0003 P2-1).
    [string]$AllowedOrigin,
    # First admin. Supplying both makes the bootstrap unattended; mirrors the
    # FLOWGATE_ADMIN_* variables docker-compose.yml already uses.
    [string]$AdminUsername,
    [string]$AdminPassword,
    [string]$AdminEmail,
    [switch]$Start  # start the server in this window after setup
)

$ErrorActionPreference = 'Stop'

# Whether we may block on Read-Host. An unattended run (CI, scheduled task) must
# fall back to parameters/environment rather than hang forever on a prompt.
$Interactive = [Environment]::UserInteractive

# Parameters fall back to the environment so an unattended run can supply
# everything the same way setup.sh does.
if (-not $AdminUsername) { $AdminUsername = $env:FLOWGATE_ADMIN_USERNAME }
if (-not $AdminPassword) { $AdminPassword = $env:FLOWGATE_ADMIN_PASSWORD }
if (-not $AdminEmail)    { $AdminEmail    = $env:FLOWGATE_ADMIN_EMAIL }
if (-not $AllowedOrigin) { $AllowedOrigin = $env:ALLOWED_ORIGIN }

# 0273 NR0003 §5-1: -DbType has a default, so "not supplied" is only detectable
# via PSBoundParameters. Without this the engine was chosen silently and the
# operator was never offered the DB selection R0001 asked for.
if (-not $PSBoundParameters.ContainsKey('DbType')) {
    if ($env:DB_TYPE) {
        $DbType = $env:DB_TYPE
    } elseif ($Interactive) {
        Write-Host 'Which database should FlowGate use?'
        Write-Host '  sqlite3   file-backed, no external server (default)'
        Write-Host '  mysql     MySQL / MariaDB'
        Write-Host '  postgres  PostgreSQL'
        $answer = Read-Host 'DB type (default sqlite3)'
        if ($answer) { $DbType = $answer }
    }
    if ($DbType -notin @('sqlite3', 'sqlite', 'local', 'mysql', 'postgres')) {
        throw "Unsupported DB_TYPE='$DbType' (use sqlite3, mysql, or postgres)."
    }
}

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

# base64-encoded 32-byte key — the form the AES-GCM helpers (TOTP, git creds) expect.
function New-B64Key { & $VenvPython -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())' }

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
# TOTP secret encryption key (0273 NR0003 P1-3) — absent from every install path
# until now. The server boots without it; the failure surfaces later as a
# RuntimeError on the first 2FA enrolment. Rotating orphans stored secrets, so
# generate once, exactly like the pepper above.
if (-not (Test-EnvSet 'FLOWGATE_TOTP_ENCRYPT_KEY')) {
    Set-EnvVar 'FLOWGATE_TOTP_ENCRYPT_KEY' (New-B64Key)
}
# Git credential encryption key (P2-2) — the container entrypoint generates this,
# host installs left it blank and fell back to a plaintext key file.
if (-not (Test-EnvSet 'FLOWGATE_GIT_ENCRYPT_KEY')) {
    Set-EnvVar 'FLOWGATE_GIT_ENCRYPT_KEY' (New-B64Key)
}
# AI provider API key encryption (0371 NR0007 3) -- ai_providers.api_key is now
# stored encrypted and this key reads it back. Generate once: rotating it orphans
# every stored provider key, so back it up together with the DB.
if (-not (Test-EnvSet 'FLOWGATE_AI_ENCRYPT_KEY')) {
    Set-EnvVar 'FLOWGATE_AI_ENCRYPT_KEY' (New-B64Key)
}

# Listen address — recorded in .env so server\stg.py and run.bat agree (P1-2).
Set-EnvVar 'FLOWGATE_PORT' "$Port"
Set-EnvVar 'FLOWGATE_BIND_HOST' $BindHost

# CORS (P2-1, hardened 0371 NR0007 §2). A blank ALLOWED_ORIGIN now fails closed --
# no cross-origin browser request is allowed until an operator opts in.
if (-not $AllowedOrigin -and $Interactive) {
    Write-Host ''
    Write-Host 'Service URL browsers will load FlowGate from (used as the CORS origin).'
    Write-Host 'Example: https://flowgate.example.com   - leave blank to block all cross-origin requests.'
    $AllowedOrigin = Read-Host 'Service URL'
}
if ($AllowedOrigin) {
    Set-EnvVar 'ALLOWED_ORIGIN' $AllowedOrigin
} else {
    Set-EnvVar 'ALLOWED_ORIGIN' ''
    Write-Host '[i] ALLOWED_ORIGIN is unset - cross-origin browser requests are blocked.'
    Write-Host "    The installed client is same-origin and unaffected. Set ALLOWED_ORIGIN in $EnvFile"
    Write-Host "    to your service URL to allow a separately-hosted client, or to '*' to allow any origin."
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
# --timeout-graceful-shutdown 3 must stay in the emitted launcher: uvicorn's
# default is an UNBOUNDED wait, so Ctrl+C / a Windows shutdown hangs on any
# in-flight request - in practice an open SSE stream (group 0102 R0001). The
# app-level shutdown_event cannot rescue this: uvicorn runs lifespan shutdown
# (which sets the event) only AFTER the wait it is meant to end. run.bat is
# gitignored and regenerated from here, so THIS is the only place the flag can
# live durably on Windows.
$runBat = @"
@echo off
REM Generated by setup.ps1 - starts the FlowGate server.
setlocal
set "FLOWGATE_STORAGE_DIR=$StorageDir"

REM Build the client so server serves the latest client/dist.
cd /d "%~dp0client"
call npm run build || (echo Client build failed & exit /b 1)

cd /d "%~dp0server"
"$VenvPython" -m uvicorn routers.main:app --host $BindHost --port $Port --timeout-graceful-shutdown 3
"@
# .bat files MUST use CRLF: cmd.exe seeks by byte offset assuming 2-byte line
# endings, so LF-only files drift and execute mid-line fragments (e.g. a stray
# 'M' from "REM"/"-m"). Normalize regardless of how this .ps1 is saved.
$runBat = $runBat -replace "`r?`n", "`r`n"
[System.IO.File]::WriteAllText((Join-Path $Root 'run.bat'), $runBat, (New-Object System.Text.UTF8Encoding($false)))

# ── admin account (every engine) ─────────────────────────────────────────────
Write-Host '==> Admin account'
# 0273 NR0003 P1-1: this used to run for sqlite only, because create_dev_user.py
# opened the DB with `import sqlite3`. A completed -DbType postgres install
# therefore had no account that could log in. Both that script and
# check_db_ready.py are engine-neutral now (server\db_bootstrap.py).
$isSqlite = $DbType -in 'sqlite3', 'sqlite', 'local'
# Storage dir goes in this scope rather than a Start-Process parameter: the
# -Environment parameter is PowerShell 7.4+ only and hard-fails on Windows
# PowerShell 5.1. Child processes inherit it, and create_dev_user.py below
# needs it too, whether or not we boot the server here.
$env:FLOWGATE_STORAGE_DIR = $StorageDir
$readyCheck = Join-Path $Root 'server\check_db_ready.py'
# Readiness is "every migration committed", NOT "the DB answers": SQLite creates
# flowgate.db the instant sqloader connects, and a networked engine accepts
# connections long before 004_rbac.sql has seeded the __SYSTEM__ project and the
# role rows — create_dev_user.py then died on a FOREIGN KEY violation.
# --db is SQLite-only; the networked engines are located via the DB_* keys the
# script reads from server\.env.
$readyArgs = @()
if ($isSqlite) { $readyArgs += @('--db', (Join-Path $StorageDir 'flowgate.db')) }

& $VenvPython $readyCheck @readyArgs --quiet
$dbReady = ($LASTEXITCODE -eq 0)
if (-not $dbReady) {
    # Covers a first install and a re-run after this step failed partway, which
    # leaves a DB that exists but is only half migrated.
    Write-Host '    Booting the server once to apply DB migrations...'
    $proc = Start-Process -FilePath $VenvPython `
        -ArgumentList @('-m', 'uvicorn', 'routers.main:app', '--host', '127.0.0.1', '--port', "$Port",
                        '--timeout-graceful-shutdown', '3') `
        -WorkingDirectory (Join-Path $Root 'server') `
        -PassThru -WindowStyle Hidden
    # Applying the full migration set takes appreciably longer than the 30s the
    # old file-existence probe needed; poll until it actually finishes.
    & $VenvPython $readyCheck @readyArgs --wait 300
    $dbReady = ($LASTEXITCODE -eq 0)
    # Windows has no clean SIGTERM for a hidden console process, but by here the
    # migrations have either committed or timed out, so nothing that the admin
    # bootstrap depends on is lost by force-stopping.
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
}
if ($dbReady) {
    $adminUser = $AdminUsername
    $adminPw   = $AdminPassword
    if (-not $adminUser -and $Interactive) { $adminUser = Read-Host 'Admin username (default admin)' }
    if (-not $adminUser) { $adminUser = 'admin' }
    if (-not $adminPw -and $Interactive) {
        while (-not $adminPw) {
            $sec = Read-Host 'Admin password' -AsSecureString
            $adminPw = [System.Net.NetworkCredential]::new('', $sec).Password
        }
    }
    # The email was hardcoded to <username>@flowgate.local; make it overridable.
    $adminMail = if ($AdminEmail) { $AdminEmail } else { "$adminUser@flowgate.local" }
    if (-not $adminPw) {
        Write-Host '[!] No admin password given (use -AdminPassword / $env:FLOWGATE_ADMIN_PASSWORD for unattended installs).'
        Write-Host "    `"$VenvPython`" server\create_dev_user.py --username admin --email admin@flowgate.local --password <pw> --admin"
    } else {
        # Skips automatically if the account already exists (re-run safe).
        & $VenvPython (Join-Path $Root 'server\create_dev_user.py') `
            --username $adminUser `
            --email $adminMail `
            --password $adminPw `
            --admin
    }

    # ── AI provider (0292 T0003) ─────────────────────────────────────────────
    # An install used to finish with an EMPTY ai_providers table, so nothing
    # AI-driven worked until someone found the settings screen — and the omission
    # only showed up later as a run dying with "all_providers_failed".
    #
    # Deliberately just y/n here. Which provider, which command and which key are
    # all asked by seed_ai_provider.py, so the prompts exist once instead of once
    # per shell, and adding a provider kind never touches this file (CH0002).
    Write-Host '==> AI provider'
    $seedAi = $false
    if ($env:FLOWGATE_AI_KIND -or $env:FLOWGATE_AI_EXEC_TYPE -or $env:FLOWGATE_AI_CLI_COMMAND) {
        # Preset for an unattended install — seed without asking, mirroring how
        # $env:FLOWGATE_ADMIN_* skips the admin prompts above.
        $seedAi = $true
    } elseif ($Interactive) {
        $seedAi = (Read-Host 'Register an AI provider now? (y/N)') -match '^[Yy]'
    }
    if ($seedAi) {
        # Skips a provider that is already registered (re-run safe), and a failed
        # probe must not fail the install — the provider is stored either way.
        # try/catch is setup.sh's `|| true`: with $ErrorActionPreference = 'Stop',
        # PowerShell 7.4+ turns a non-zero native exit into a terminating error.
        try { & $VenvPython (Join-Path $Root 'server\seed_ai_provider.py') }
        catch { Write-Host "[!] AI provider setup did not complete: $_" }
    } else {
        Write-Host '    Skipped. Register one whenever you like — this runs exactly the'
        Write-Host '    step that was just declined:'
        Write-Host "    $Root\setup-ai.ps1"
    }
} else {
    Write-Host '[!] DB migrations did not finish — create the admin account manually later'
    Write-Host '    (start the server, let it finish migrating, then run):'
    Write-Host "    `"$VenvPython`" server\create_dev_user.py --username admin --email admin@flowgate.local --password <pw> --admin"
    Write-Host '    ...and register an AI provider with:'
    Write-Host "    $Root\setup-ai.ps1"
}

# ── done ─────────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '──────────────────────────────────────────────────────────────'
Write-Host @"

Done. Start FlowGate with:

  run.bat                 (or: .\.venv\Scripts\python -m uvicorn routers.main:app --host 0.0.0.0 --port $Port --timeout-graceful-shutdown 3  from server\)

  Open:     http://localhost:$Port
  Listen:   ${BindHost}:$Port
  DB:       $DbType (schema auto-migrates on first boot from sql\migrations\)
  CORS:     $(if ($AllowedOrigin) { $AllowedOrigin } else { '* (any origin)' })
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
        & $VenvPython -m uvicorn routers.main:app --host $BindHost --port $Port --timeout-graceful-shutdown 3
    } finally {
        Pop-Location
    }
}
