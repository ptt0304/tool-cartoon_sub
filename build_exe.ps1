$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildVenv = Join-Path $root ".build-venv"
$python = Join-Path $buildVenv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv $buildVenv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create build environment: $buildVenv"
    }
}

Push-Location $root
try {
    & $python -m pip install --disable-pip-version-check "PyInstaller==6.21.0" .
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install build dependencies"
    }
    & $python -m PyInstaller --clean --noconfirm .\Cartoon_Sub.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
