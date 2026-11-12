# Build the Windows package into dist\AndroidDoctorPro\ and a zip next to it.
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Requires: pip install -r requirements.txt -r packaging\requirements-build.txt
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m unittest discover -s tests -t .
if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }

python companion\build.py
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Companion APK not built (Android SDK / JDK missing?): the package will not include it."
}

python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\android_doctor.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$out = Join-Path $root "dist\AndroidDoctorPro"
& (Join-Path $out "adp-cli.exe") --check-bins | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Bundled tools do not match bin\manifest.json inside the build." }

$version = (Get-Date -Format "yyyy.MM.dd")
$zip = Join-Path $root "dist\AndroidDoctorPro-$version-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Built: $out"
Write-Host "Zip:   $zip"
