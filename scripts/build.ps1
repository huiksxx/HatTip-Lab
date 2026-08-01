param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Artifact = Join-Path $ProjectDir "HatTipLab-windows-x64.zip"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & (Join-Path $PSScriptRoot "setup.ps1")
}

if (-not $SkipInstall) {
    & $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements-build.txt")
}

& $VenvPython -m unittest discover -s (Join-Path $ProjectDir "tests") -v
& $VenvPython (Join-Path $ProjectDir "tools\release_audit.py")
& $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $ProjectDir "HatTipLab.spec")
& $VenvPython (Join-Path $ProjectDir "tools\release_audit.py") (Join-Path $ProjectDir "dist\HatTipLab")

Compress-Archive -Path (Join-Path $ProjectDir "dist\HatTipLab\*") -DestinationPath $Artifact -Force
Write-Host "Built: $Artifact"
