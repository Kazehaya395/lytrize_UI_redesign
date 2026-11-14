#Requires -Version 5.1
<#
.SYNOPSIS
  Build a standalone Windows executable of the Lytrize desktop app.

.DESCRIPTION
  Creates a dedicated build venv (.venv-build, kept separate from the dev
  .venv), installs requirements.txt + PyInstaller into it, then freezes
  desktop/app.py into

      dist\Lytrize\Lytrize.exe        (default, --onedir)

  All runtime assets (backend\assets: echarts, maps, fonts, icons) are bundled
  so the exe runs with no Python install. OneDrive-ish paths with spaces are
  fine; the output folder can be zipped and copied to any Windows PC.
  Add -MakeInstaller to also compile Lytrize.iss with Inno Setup 6 / 7
  (ISCC.exe auto-detected, e.g. C:\Program Files\Inno Setup 7) into
  dist\Lytrize-Setup-<version>.exe -- a per-user installer that needs no
  admin rights and upgrades previous installs in place.

  Notes:
    * PySide6-WebEngine is collected in full (QtWebEngineProcess.exe, ICU
      data, resources) -- this is what makes the charts work offline.
    * polars and pyarrow are collected in full as well (native extensions).
    * The icon is backend\assets\lytrize.ico.
    * First run on a machine SmartScreen flags the unsigned exe; choose
      "More info -> Run anyway", or code-sign the exe for distribution.

.PARAMETER OneFile
  Produce a single dist\Lytrize.exe instead of a folder. The file is bigger
  and starts slower (Chromium/WebEngine is extracted to a temp dir at every
  launch); --onedir is the recommended default.

.PARAMETER SkipInstall
  Do not create/refresh .venv-build or pip install (assumes it is ready).

.PARAMETER KeepConsole
  Build with a console window (useful for seeing tracebacks/log output).
  Default is a windowed GUI build with no console.

.PARAMETER Clean
  Also delete build\ and dist\ before building (always done for the
  PyInstaller cache via --clean; this adds the output folders).

.PARAMETER MakeInstaller
  After the exe build succeeds, compile Lytrize.iss with Inno Setup 6 / 7 to
  produce dist\Lytrize-Setup-<AppVersion>.exe (per-user install, no admin
  required). Only works with the default folder build, not -OneFile.
  ISCC.exe is auto-detected in the standard install locations (including
  C:\Program Files\Inno Setup 7); override with -IsccPath if yours lives
  elsewhere.

.PARAMETER AppVersion
  Version stamped into the installer (default: 1.0.0). Overridden if you pass
  it; the exe itself carries no version resource.

.PARAMETER IsccPath
  Full path to ISCC.exe, e.g. "C:\Program Files\Inno Setup 7\ISCC.exe".
  Only used with -MakeInstaller; auto-detected when omitted.

.EXAMPLE
  .\build_exe.ps1                  # folder build -> dist\Lytrize\Lytrize.exe
  .\build_exe.ps1 -MakeInstaller   # ...then also build the installer
  .\build_exe.ps1 -MakeInstaller -AppVersion 1.2.0
  .\build_exe.ps1 -OneFile         # single-file build
  .\build_exe.ps1 -KeepConsole     # debug build with a console
#>
[CmdletBinding()]
param(
    [switch]$OneFile,
    [switch]$SkipInstall,
    [switch]$KeepConsole,
    [switch]$Clean,
    [switch]$MakeInstaller,
    [string]$AppVersion = '1.0.0',
    [string]$IsccPath = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($MakeInstaller -and $OneFile) {
    throw "-MakeInstaller is only supported with the default folder build (Lytrize.iss packages dist\Lytrize\*). Drop -OneFile, or run ISCC manually for a one-file layout."
}

$RepoRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot
$AppName   = 'Lytrize'
$BuildVenv = Join-Path $RepoRoot '.venv-build'
$VenvPython = Join-Path $BuildVenv 'Scripts\python.exe'
$BuildDir  = Join-Path $RepoRoot 'build'
$DistDir   = Join-Path $RepoRoot 'dist'

Write-Host "======================================"
Write-Host "  Lytrize Windows exe builder"
Write-Host "  Output: $DistDir\$AppName\"
Write-Host "======================================"

# --- Locate Python 3.11+ ------------------------------------------------------
function Find-Python {
    $candidates = @(
        @{ Exe = 'py';      Args = @('-3') },
        @{ Exe = 'python';  Args = @() },
        @{ Exe = 'python3'; Args = @() }
    )
    foreach ($c in $candidates) {
        try {
            $version = & $c.Exe @($c.Args + @('--version')) 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $version) { continue }
            if ($version -match '(\d+)\.(\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                    return @{ Exe = $c.Exe; Args = $c.Args; Version = $version.Trim() }
                }
                Write-Host "  Found $($version.Trim()) via $($c.Exe) -- too old, need 3.11+."
            }
        } catch { continue }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "ERROR: Python 3.11+ not found. Install from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
Write-Host "  Python: $($python.Version) ($($python.Exe))"

# --- Build venv with runtime deps + PyInstaller ---------------------------------
if (-not $SkipInstall) {
    if (-not (Test-Path $VenvPython)) {
        Write-Host "  Creating build venv: $BuildVenv"
        & $python.Exe @($python.Args + @('-m', 'venv', $BuildVenv))
        if ($LASTEXITCODE -ne 0) { throw "build venv creation failed" }
    } else {
        Write-Host "  Using build venv:    $VenvPython"
    }
    Write-Host "  Installing requirements + PyInstaller (first run can take a while)..."
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -r (Join-Path $RepoRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw "pip install of requirements.txt failed" }
    & $VenvPython -m pip install 'pyinstaller>=6.0'
    if ($LASTEXITCODE -ne 0) { throw "pip install of pyinstaller failed" }
} else {
    if (-not (Test-Path $VenvPython)) {
        Write-Host "ERROR: -SkipInstall given but $VenvPython does not exist." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Skipping install; using $VenvPython"
}

# --- Inno Setup compiler check (fail fast, before the long PyInstaller run) ----------
$IsccExe = $null
if ($MakeInstaller) {
    $candidates = @()
    if ($IsccPath) { $candidates += $IsccPath }
    $candidates += @(
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 5\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { $IsccExe = $c; break }
    }
    if (-not $IsccExe) {
        throw "Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6/7 from https://jrsoftware.org/isdl.php or pass -IsccPath 'C:\Path\To\ISCC.exe'."
    }
    $IssFile = Join-Path $RepoRoot 'Lytrize.iss'
    if (-not (Test-Path $IssFile)) { throw "Lytrize.iss not found at $IssFile" }
    Write-Host "  Installer: $IsccExe"
}

# --- Preflight -------------------------------------------------------------------
$Assets = Join-Path $RepoRoot 'backend\assets'
if (-not (Test-Path $Assets)) { throw "backend\\assets not found -- is this the repo root?" }
$Icon = Join-Path $RepoRoot 'backend\assets\lytrize.ico'
if (-not (Test-Path $Icon)) { Write-Host "  WARNING: $Icon not found; exe will use the default icon" -ForegroundColor Yellow; $Icon = $null }

if ($Clean) {
    Write-Host "  Removing previous build outputs..."
    Remove-Item -Recurse -Force $BuildDir, $DistDir -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $BuildDir, $DistDir | Out-Null

# --- Entry stub ------------------------------------------------------------------
# desktop/app.py resolves paths from __file__; inside the frozen bundle that is
# <bundle>/desktop/app.py, so BASE/backend lookups land exactly where the
# backend\assets data files are unpacked. The stub only needs to put the repo
# root (and backend/) on sys.path before importing the app.
$Entry = Join-Path $BuildDir '_lytrize_entry.py'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$EntryCode = @'
import os
import sys
from pathlib import Path

_FROZEN = getattr(sys, "frozen", False)
if _FROZEN:
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    ROOT = Path(r"__REPO_ROOT__")
BACKEND = ROOT / "backend"
for _p in (str(ROOT), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
'@.Replace('__REPO_ROOT__', $RepoRoot)
[System.IO.File]::WriteAllText($Entry, $EntryCode, $Utf8NoBom)
Write-Host "  Entry stub: $Entry"

# --- Assemble PyInstaller arguments ----------------------------------------------
$pyiArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm', '--clean',
    '--name', $AppName,
    '--log-level', 'INFO',
    # Repo root + backend/ on the analysis path so both `desktop.*` and the
    # bare top-level modules the app imports (services.*, modules.*, font_bundle,
    # chart_display, config, ...) resolve at build time.
    '--paths', $RepoRoot,
    '--paths', (Join-Path $RepoRoot 'backend'),
    # Bundled assets: charts (echarts + maps), fonts, icons. Everything the app
    # opens from the filesystem lives under backend\assets.
    '--add-data', "$Assets;backend\assets",
    # Heavy native stacks that PyInstaller's hooks do not fully scrape:
    '--collect-all', 'PySide6',
    '--collect-all', 'polars',
    '--collect-all', 'pyarrow',
    # Trim things the app never uses:
    '--exclude-module', 'tkinter',
    '--exclude-module', 'pytest',
    '--exclude-module', '_pytest'
)
if ($Icon) { $pyiArgs += @('--icon', $Icon) }
if (-not $KeepConsole) { $pyiArgs += '--windowed' }
if ($OneFile) { $pyiArgs += '--onefile' } else { $pyiArgs += '--onedir' }
$pyiArgs += $Entry

Write-Host ""
Write-Host "Running PyInstaller ($($pyiArgs.Count) arguments)..."
& $VenvPython @pyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed -- see the errors above" }

# --- Report -------------------------------------------------------------------------
if ($OneFile) {
    $ExePath = Join-Path $DistDir "$AppName.exe"
} else {
    $ExePath = Join-Path $DistDir "$AppName\$AppName.exe"
}
if (-not (Test-Path $ExePath)) { throw "Build reported success but $ExePath is missing" }

$InstallerPath = $null
if ($MakeInstaller) {
    Write-Host ""
    Write-Host "Running Inno Setup -> dist\Lytrize-Setup-$AppVersion.exe ..."
    & $IsccExe "/DAppVersion=$AppVersion" (Join-Path $RepoRoot 'Lytrize.iss')
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed -- see the output above" }
    $iPath = Join-Path $DistDir "Lytrize-Setup-$AppVersion.exe"
    if (-not (Test-Path $iPath)) { throw "Inno Setup reported success but $iPath is missing" }
    $InstallerPath = $iPath
}

$size = '{0:N1} MB' -f ((Get-Item $ExePath).Length / 1MB)
Write-Host ""
Write-Host "======================================"
Write-Host "  Build complete!"
Write-Host "  Exe    : $ExePath"
Write-Host "  Size   : $size"
if (-not $OneFile) {
    $folderSize = '{0:N1} MB' -f (((Get-ChildItem (Join-Path $DistDir $AppName) -Recurse -File |
        Measure-Object -Property Length -Sum).Sum) / 1MB)
    Write-Host "  Folder : $(Join-Path $DistDir $AppName)  ($folderSize total)"
    if ($InstallerPath) {
        $iSize = '{0:N1} MB' -f ((Get-Item $InstallerPath).Length / 1MB)
        Write-Host "  Setup  : $InstallerPath  ($iSize)"
        Write-Host "  Note   : per-user install, no admin rights required; the AppId is"
        Write-Host "           fixed, so a newer installer upgrades the old one in place."
    } else {
        Write-Host "  Ship   : zip the whole '$AppName' folder, or re-run with -MakeInstaller"
        Write-Host "           to produce the Inno Setup installer instead."
    }
}
Write-Host ""
Write-Host "  Smoke  : double-click the exe, check that charts, fonts, maps and"
Write-Host "           exports work (they all load from backend\\assets)."
Write-Host "======================================"
exit 0
