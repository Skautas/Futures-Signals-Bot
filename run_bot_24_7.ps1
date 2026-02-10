$ErrorActionPreference = "Continue"

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

# Stop existing bot processes to avoid duplicates
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*futures_signals.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Run bot in background with logs appended
& $python "futures_signals.py" *>> "bot.log"
