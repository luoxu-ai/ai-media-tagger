$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$ExifToolVersion = "13.59"
$DownloadDir = Join-Path $ProjectDir "_exiftool"
$ZipPath = Join-Path $ProjectDir "exiftool.zip"
$VendorDir = Join-Path $ProjectDir "vendor"

if (Get-Command python.exe -ErrorAction SilentlyContinue) {
    & python.exe -m venv $VenvDir
}
elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    & py.exe -3 -m venv $VenvDir
}
else {
    throw "Python 3.11 or 3.12 was not found. Install Python and try again."
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt")

$DownloadUrl = "https://master.dl.sourceforge.net/project/exiftool/exiftool-$($ExifToolVersion)_64.zip?viasf=1"
Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing

if (Test-Path $DownloadDir) {
    Remove-Item -LiteralPath $DownloadDir -Recurse -Force
}
Expand-Archive -LiteralPath $ZipPath -DestinationPath $DownloadDir -Force
New-Item -ItemType Directory -Path $VendorDir -Force | Out-Null

$Exe = Get-ChildItem $DownloadDir -Recurse -Filter "exiftool(-k).exe" | Select-Object -First 1
$Files = Get-ChildItem $DownloadDir -Recurse -Directory -Filter "exiftool_files" | Select-Object -First 1
if (-not $Exe -or -not $Files) {
    throw "The downloaded ExifTool package is incomplete."
}

Copy-Item -LiteralPath $Exe.FullName -Destination (Join-Path $VendorDir "exiftool.exe") -Force
Copy-Item -LiteralPath $Files.FullName -Destination (Join-Path $VendorDir "exiftool_files") -Recurse -Force
Write-Host "Dependencies are ready."
