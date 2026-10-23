#requires -Version 5.1
<#
Builds the Windows installer: a fresh virtual environment, the PyInstaller bundle, then the Inno Setup installer.
Run it from anywhere: .\packaging\build.ps1 [-Python path\to\python.exe]
If script execution is disabled: powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1
The installer is written to dist\PlaylistCreator-<version>-setup.exe, the version coming from pyproject.toml.
#>
param(
    # Interpreter that creates the build environment; the bundle ships its own copy, so users need no Python
    [string]$Python = "python",
    [string]$RequiredVersion = "3.13"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$work = Join-Path $PSScriptRoot "_work"
$venv = Join-Path $work "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "== $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

# Look for Inno Setup first, so a missing install fails before the slow steps
$innoFolders = "${env:ProgramFiles(x86)}", $env:ProgramFiles, "$env:LOCALAPPDATA\Programs" |
    ForEach-Object { Get-ChildItem $_ -Directory -Filter "Inno Setup *" -ErrorAction SilentlyContinue } |
    Sort-Object Name -Descending
$innoCandidates = @(Get-Command ISCC.exe -ErrorAction SilentlyContinue | ForEach-Object Source) +
    @($innoFolders | ForEach-Object { Join-Path $_.FullName "ISCC.exe" })
$iscc = $innoCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup was not found. Install it from https://jrsoftware.org/isinfo.php and run this script again."
}

Push-Location $root
try {
    $found = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($LASTEXITCODE -ne 0 -or $found -ne $RequiredVersion) {
        throw "Python $RequiredVersion is required to build, but '$Python' is $found. Pass -Python with the path to a Python $RequiredVersion interpreter."
    }

    if (Test-Path $venv) { Remove-Item $venv -Recurse -Force }
    Invoke-Step "Create the build environment (Python $found)" { & $Python -m venv $venv }
    Invoke-Step "Install the project and PyInstaller" { & $venvPython -m pip install --quiet . pyinstaller }

    $version = & $venvPython -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
    if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Could not read the version from pyproject.toml" }

    # A conda Python keeps the DLLs of its standard library (libffi, OpenSSL, ...) in Library\bin, which PyInstaller only finds through PATH
    $basePrefix = & $venvPython -c "import sys; print(sys.base_prefix)"
    $condaBin = Join-Path $basePrefix "Library\bin"
    if (Test-Path $condaBin) { $env:PATH = "$condaBin;$env:PATH" }

    # PyInstaller cannot delete its previous output inside a OneDrive-synced folder (access denied), so clear it here
    $bundle = Join-Path $root "dist\PlaylistCreator"
    if (Test-Path $bundle) { Remove-Item $bundle -Recurse -Force }
    Invoke-Step "Build the bundle" {
        & $venvPython -m PyInstaller --noconfirm --distpath dist --workpath (Join-Path $work "pyinstaller") (Join-Path $PSScriptRoot "playlist_creator.spec")
    }
    Invoke-Step "Build the installer $version" { & $iscc "/DAppVersion=$version" (Join-Path $PSScriptRoot "installer.iss") }

    Write-Host "Done: $(Join-Path $root "dist\PlaylistCreator-$version-setup.exe")"
}
finally {
    Pop-Location
}
