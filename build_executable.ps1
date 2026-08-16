param(
    [switch]$SkipDependencyCheck
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$version = (Get-Content -LiteralPath (Join-Path $projectRoot 'VERSION') -Raw).Trim()
$buildRoot = Join-Path $projectRoot 'build\pyinstaller'
$outputRoot = Join-Path $projectRoot 'build\executable'
$workRoot = Join-Path $buildRoot 'work'
$specRoot = Join-Path $buildRoot 'spec'
$versionFile = Join-Path $buildRoot 'droste-version-info.txt'
$executablePath = Join-Path $outputRoot 'Droste.exe'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Development environment is missing. Run setup.bat first.'
}

if (-not $SkipDependencyCheck) {
    & $pythonPath -c "import PyInstaller; raise SystemExit(0 if PyInstaller.__version__ == '6.21.0' else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw 'Install build dependencies with: .\.venv\Scripts\python.exe -m pip install -r requirements-build.lock.txt'
    }
}

$versionParts = @($version.Split('.') | ForEach-Object { [int]$_ })
if ($versionParts.Count -lt 2 -or $versionParts.Count -gt 4) {
    throw "VERSION must contain two to four numeric components: $version"
}
while ($versionParts.Count -lt 4) {
    $versionParts += 0
}
$versionTuple = $versionParts -join ', '

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
New-Item -ItemType Directory -Path $specRoot -Force | Out-Null

$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($versionTuple),
    prodvers=($versionTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '041104B0',
        [
          StringStruct('CompanyName', 'Droste'),
          StringStruct('FileDescription', 'Droste LAN screen viewer'),
          StringStruct('FileVersion', '$version'),
          StringStruct('InternalName', 'Droste'),
          StringStruct('OriginalFilename', 'Droste.exe'),
          StringStruct('ProductName', 'Droste'),
          StringStruct('ProductVersion', '$version')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1041, 1200])])
  ]
)
"@
Set-Content -LiteralPath $versionFile -Value $versionInfo -Encoding utf8

$entryPoint = Join-Path $projectRoot 'droste_tray.py'
$iconPath = Join-Path $projectRoot 'droste.ico'
$staticPath = Join-Path $projectRoot 'static'

& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --noupx `
    --name Droste `
    --icon $iconPath `
    --add-data "$staticPath;static" `
    --add-data "$iconPath;." `
    --version-file $versionFile `
    --distpath $outputRoot `
    --workpath $workRoot `
    --specpath $specRoot `
    $entryPoint
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller failed to build Droste.exe.'
}
if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
    throw "Executable was not created: $executablePath"
}

$signature = Get-AuthenticodeSignature -FilePath $executablePath
Write-Host "Executable created: $executablePath"
Write-Host "SHA-256: $((Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant())"
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    Write-Warning 'Droste.exe is not code-signed. Sign the final public release before broad distribution.'
}
