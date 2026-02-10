$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

$venvPath = Join-Path $repo ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "WARNING: .venv not found, creating a new virtual env..."
    python -m venv ".venv"
}

if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = "python"
}

# Ensure pip is available
& $python -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    & $python -m ensurepip --upgrade
}

# Install dependencies if needed
& $python -c "import ccxt, pandas, numpy, ta, telegram, flask, qrcode, pytz, dotenv" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r "requirements.txt"
}

# Run bot and show output in this terminal while also saving to bot.log
$ErrorActionPreference = "Continue"
& $python "futures_signals.py" 2>&1 | Tee-Object -FilePath "bot.log"
