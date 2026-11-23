# Terminal component installer: [--from-dir DIRECTORY]
#Requires -Version 5.1
[CmdletBinding()]
param(
    # Everything is forwarded verbatim to setup_terminal.py. Declared as a
    # catch-all rather than as typed parameters so the option list lives in one
    # place — adding a flag to the Python script needs no change here.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SeedArgs
)

$ErrorActionPreference = 'Stop'

$Root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$Seed       = Join-Path $Root 'server\setup_terminal.py'

if (-not (Test-Path $Seed)) {
    Write-Host "[!] $Seed not found - run this from a FlowGate checkout."
    exit 1
}

# The venv is what setup.ps1 builds and what run.bat uses, so it is the interpreter
# that actually has the server's dependencies. Fall back to an ambient python only so
# a container/manual install (deps already on the system interpreter) is not locked out.
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $fallback = @('py', 'python') |
        ForEach-Object { Get-Command $_ -ErrorAction SilentlyContinue } |
        Select-Object -First 1
    if (-not $fallback) {
        Write-Host '[!] No Python found. Install Python 3 (python.org) or run .\setup.ps1 first.'
        exit 1
    }
    $Python = $fallback.Source
    Write-Host "[!] No venv at $Root\.venv - falling back to $Python."
    Write-Host '    If this fails on a missing import, run .\setup.ps1 first.'
}

# No try/catch here, unlike the call inside setup.ps1: there the install must survive
# a provider that would not register, while here registering IS the job, so the exit
# code is the answer the caller wants.
& $Python $Seed @SeedArgs
exit $LASTEXITCODE
