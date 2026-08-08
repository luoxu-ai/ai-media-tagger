$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".train_venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
$Version = "13.59"
$Zip = "$ProjectDir\exiftool.zip"
$Extracted = "$ProjectDir\_exiftool"

& $Python -m pip install --disable-pip-version-check -r "$ProjectDir\requirements-build.txt"
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
if ((Test-Path -LiteralPath "$ProjectDir\vendor\exiftool.exe") -and (Test-Path -LiteralPath "$ProjectDir\vendor\exiftool_files")) {
    Write-Host "Pinned Python dependencies and bundled ExifTool are ready."
    exit 0
}
& curl.exe -L --fail --retry 2 -A "Mozilla/5.0" "https://master.dl.sourceforge.net/project/exiftool/exiftool-$($Version)_64.zip?viasf=1" -o $Zip
if ($LASTEXITCODE -ne 0) { throw "ExifTool download failed." }
Expand-Archive -LiteralPath $Zip -DestinationPath $Extracted -Force
New-Item -ItemType Directory -Path "$ProjectDir\vendor" -Force | Out-Null
$Exe = Get-ChildItem $Extracted -Recurse -Filter "exiftool(-k).exe" | Select-Object -First 1
$Files = Get-ChildItem $Extracted -Recurse -Directory -Filter "exiftool_files" | Select-Object -First 1
Copy-Item -LiteralPath $Exe.FullName -Destination "$ProjectDir\vendor\exiftool.exe" -Force
Copy-Item -LiteralPath $Files.FullName -Destination "$ProjectDir\vendor\exiftool_files" -Recurse -Force
Write-Host "Dependencies are ready."
