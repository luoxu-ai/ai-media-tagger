$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$ExifTool = Join-Path $ProjectDir "vendor\exiftool.exe"

if (-not (Test-Path $Python)) {
    throw "Missing .venv. Run .\prepare.ps1 first."
}
if (-not (Test-Path $ExifTool)) {
    throw "Missing ExifTool. Run .\prepare.ps1 first."
}

Push-Location $ProjectDir
try {
    & $Python -m PyInstaller --noconfirm --clean (Join-Path $ProjectDir "release.spec")
}
finally {
    Pop-Location
}

Write-Host "Build completed in: $ProjectDir\dist"

