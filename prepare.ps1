$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".train_venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
$Version = "13.59"
$ExpectedSha256 = "577257dd22baebe77157d905792d6ed2c5916cd03aa627f40b2175db12110ac6"
$Zip = "$ProjectDir\exiftool.zip"
$Extracted = "$ProjectDir\_exiftool"
$DownloadUrls = @(
    "https://oliverbetz.de/cms/files/Artikel/ExifTool-for-Windows/exiftool-$($Version)_64.zip",
    "https://sourceforge.net/projects/exiftool/files/exiftool-$($Version)_64.zip/download"
)

& $Python -m pip install --disable-pip-version-check -r "$ProjectDir\requirements-build.txt"
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
if ((Test-Path -LiteralPath "$ProjectDir\vendor\exiftool.exe") -and (Test-Path -LiteralPath "$ProjectDir\vendor\exiftool_files")) {
    Write-Host "Pinned Python dependencies and bundled ExifTool are ready."
    exit 0
}
$Downloaded = $false
foreach ($Url in $DownloadUrls) {
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
    & curl.exe -L --fail --retry 2 -A "Mozilla/5.0" $Url -o $Zip
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "ExifTool download source failed: $Url"
        continue
    }
    $ActualSha256 = (Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $ExpectedSha256) {
        Write-Warning "ExifTool checksum mismatch from: $Url"
        continue
    }
    $Downloaded = $true
    break
}
if (-not $Downloaded) { throw "ExifTool download or SHA-256 verification failed." }

$ProjectRoot = [IO.Path]::GetFullPath($ProjectDir).TrimEnd('\') + '\'
$ExtractedRoot = [IO.Path]::GetFullPath($Extracted)
if (-not $ExtractedRoot.StartsWith($ProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe ExifTool extraction path: $ExtractedRoot"
}
Remove-Item -LiteralPath $ExtractedRoot -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath $Zip -DestinationPath $Extracted -Force
New-Item -ItemType Directory -Path "$ProjectDir\vendor" -Force | Out-Null
$Exe = Get-ChildItem $Extracted -Recurse -File |
    Where-Object { $_.Name -in @("exiftool.exe", "exiftool(-k).exe") } |
    Select-Object -First 1
$Files = Get-ChildItem $Extracted -Recurse -Directory -Filter "exiftool_files" | Select-Object -First 1
if (-not $Exe -or -not $Files) {
    throw "Downloaded ExifTool archive is missing the expected executable or support folder."
}
Copy-Item -LiteralPath $Exe.FullName -Destination "$ProjectDir\vendor\exiftool.exe" -Force
Copy-Item -LiteralPath $Files.FullName -Destination "$ProjectDir\vendor\exiftool_files" -Recurse -Force
Write-Host "Dependencies are ready."
