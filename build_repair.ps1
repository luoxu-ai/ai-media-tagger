$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".train_venv\Scripts\python.exe"

if (-not (Test-Path "$ProjectDir\repair_payload")) {
    throw "Missing repair_payload directory."
}

Push-Location $ProjectDir
try {
    & $Python -m PyInstaller --noconfirm --clean "$ProjectDir\update_repair.spec"
}
finally {
    Pop-Location
}

Write-Host "Repair utility completed: $ProjectDir\dist\AI-Media-Tagger-Update-Repair.exe"
