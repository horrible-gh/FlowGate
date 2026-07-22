<#
.SYNOPSIS
    FlowGate AI provider setup (Windows) — the standalone counterpart to setup.ps1.

.DESCRIPTION
    setup.ps1 installs FlowGate and asks one y/n question about registering the first
    AI provider. This script is that same step on its own, for when you:
      - answered "n" during the install and want a provider now
      - want to add a second/third provider to the fallback chain
      - installed some other way (container, manual venv) and never saw the prompt

    Every argument is passed straight through to server\seed_ai_provider.py, which
    holds the whole implementation — this file only locates the interpreter, exactly
    as setup.ps1 does before calling create_dev_user.py. Nothing about which providers
    exist or what their commands look like is duplicated here, so a new provider kind
    never touches this file.

.EXAMPLE
    .\setup-ai.ps1
    Interactive: pick a CLI found on PATH, or enter an API key.

.EXAMPLE
    .\setup-ai.ps1 --list
    Show the providers that are already registered.

.EXAMPLE
    .\setup-ai.ps1 --kind claude
    Register Claude Code using the documented command for this host, as-is.

.EXAMPLE
    .\setup-ai.ps1 --exec-type api --kind openai --api-model gpt-5.6-sol
    Register an API provider (the key is asked for without echoing).

.EXAMPLE
    .\setup-ai.ps1 --help
    List every option and every FLOWGATE_AI_* environment variable.
#>
# Windows PowerShell 5.1 (the Windows default shell) and PowerShell 7.x both work.
#Requires -Version 5.1
[CmdletBinding()]
param(
    # Everything is forwarded verbatim to seed_ai_provider.py. Declared as a
    # catch-all rather than as typed parameters so the option list lives in one
    # place — adding a flag to the Python script needs no change here.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SeedArgs
)

$ErrorActionPreference = 'Stop'

$Root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$Seed       = Join-Path $Root 'server\seed_ai_provider.py'

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
