$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

# Stop any running bot/watchdog processes without CIM/WMI
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
try {
    Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $venvPython } |
        Stop-Process -Force -ErrorAction SilentlyContinue
} catch {
    # Ignore access issues
}

# Fallback: taskkill by module path (if available)
try {
    & taskkill /F /IM python.exe /FI "MODULES eq $venvPython" | Out-Null
} catch {
    # Ignore failures
}

# Remove lock files (if present)
$tempLock = Join-Path $env:TEMP "futures_signals.lock"
if (Test-Path $tempLock) {
    Remove-Item $tempLock -Force -ErrorAction SilentlyContinue
}

$watchdogLock = Join-Path $repo "bot\bot.lock"
if (Test-Path $watchdogLock) {
    Remove-Item $watchdogLock -Force -ErrorAction SilentlyContinue
}

Write-Host "✅ Bot stopped and lock files cleared."
