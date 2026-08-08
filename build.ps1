$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".train_venv\Scripts\python.exe"

if (-not (Test-Path "$ProjectDir\vendor\exiftool.exe")) {
    throw "Missing vendor\exiftool.exe. Run prepare.ps1 first."
}

Push-Location $ProjectDir
try {
    # The ASCII spec filename prevents Windows PowerShell 5 from corrupting
    # the Unicode executable name when this UTF-8 script is invoked.
    & $Python -m PyInstaller --noconfirm --clean "$ProjectDir\release.spec"
}
finally {
    Pop-Location
}

Write-Host "Build completed: $ProjectDir\dist\AI媒体标签工具.exe"
