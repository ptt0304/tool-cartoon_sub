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
    & $python -c "import PyInstaller, PySide6, pysubs2, dotenv, keyring, google.genai, httpx, cartoon_sub"
    if ($LASTEXITCODE -ne 0) {
        & $python -m pip install --disable-pip-version-check "PyInstaller==6.21.0" .
        if ($LASTEXITCODE -ne 0) {
            throw "Could not install build dependencies"
        }
    }
    & $python -m PyInstaller --clean --noconfirm .\Cartoon_Sub.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $distRoot = Join-Path $root "dist\Cartoon_Sub"
    $distExe = Join-Path $distRoot "Cartoon_Sub.exe"
    $distInternal = Join-Path $distRoot "_internal"
    $canonicalExe = Join-Path $root "Cartoon_Sub.exe"
    $canonicalInternal = Join-Path $root "_internal"
    if (-not (Test-Path -LiteralPath $distExe) -or -not (Test-Path -LiteralPath $distInternal)) {
        throw "PyInstaller output is incomplete: $distRoot"
    }
    if (Get-Process -Name "Cartoon_Sub" -ErrorAction SilentlyContinue) {
        throw "Close Cartoon_Sub.exe before publishing the rebuilt application"
    }

    $resolvedRoot = [IO.Path]::GetFullPath($root).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $resolvedInternal = [IO.Path]::GetFullPath($canonicalInternal)
    if (-not $resolvedInternal.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe publish destination: $resolvedInternal"
    }
    if (Test-Path -LiteralPath $canonicalInternal) {
        Remove-Item -LiteralPath $canonicalInternal -Recurse -Force
    }
    Copy-Item -LiteralPath $distInternal -Destination $canonicalInternal -Recurse -Force
    Copy-Item -LiteralPath $distExe -Destination $canonicalExe -Force
    Write-Host "Published canonical executable: $canonicalExe"
} finally {
    Pop-Location
}
