param(
    [switch]$SkipExecutableBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Get-Content -LiteralPath (Join-Path $projectRoot 'VERSION') -Raw).Trim()
$distRoot = Join-Path $projectRoot 'dist'
$releaseName = "Droste-$version-windows-x64"
$stagePath = Join-Path $distRoot $releaseName
$zipPath = Join-Path $distRoot "$releaseName.zip"
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$executablePath = Join-Path $projectRoot 'build\executable\Droste.exe'

$resolvedDistRoot = [System.IO.Path]::GetFullPath($distRoot)
$resolvedStagePath = [System.IO.Path]::GetFullPath($stagePath)
if (-not $resolvedStagePath.StartsWith(
    $resolvedDistRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw 'Unsafe release staging path.'
}

if (-not $SkipExecutableBuild) {
    & (Join-Path $projectRoot 'build_executable.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'Droste.exe build failed.'
    }
}
if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
    throw 'Droste.exe is missing. Run build_executable.ps1 first.'
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Development environment is missing. Run setup.bat first.'
}

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
if (Test-Path -LiteralPath $stagePath) {
    Remove-Item -LiteralPath $stagePath -Recurse -Force
}
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
if (Test-Path -LiteralPath "$zipPath.sha256") {
    Remove-Item -LiteralPath "$zipPath.sha256" -Force
}
New-Item -ItemType Directory -Path $stagePath | Out-Null

Copy-Item -LiteralPath $executablePath -Destination (Join-Path $stagePath 'Droste.exe')
foreach ($relativePath in @(
    'VERSION',
    'README.md',
    'START-HERE.txt',
    'UNINSTALL.txt',
    'SECURITY.md',
    'THIRD-PARTY-NOTICES.txt',
    'configure_firewall.ps1',
    'remove_firewall.ps1'
)) {
    Copy-Item `
        -LiteralPath (Join-Path $projectRoot $relativePath) `
        -Destination (Join-Path $stagePath $relativePath)
}

$licenseRoot = Join-Path $stagePath 'third-party-licenses'
New-Item -ItemType Directory -Path $licenseRoot | Out-Null
$pythonBase = (& $pythonPath -c 'import sys; print(sys.base_prefix)').Trim()
$pythonLicense = Join-Path $pythonBase 'LICENSE.txt'
if (-not (Test-Path -LiteralPath $pythonLicense -PathType Leaf)) {
    throw "Python runtime license was not found: $pythonLicense"
}
$pythonLicenseDirectory = Join-Path $licenseRoot 'Python'
New-Item -ItemType Directory -Path $pythonLicenseDirectory | Out-Null
Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $pythonLicenseDirectory 'LICENSE.txt')

$runtimeDistributions = @(
    'blinker',
    'cffi',
    'cheroot',
    'click',
    'colorama',
    'cryptography',
    'Flask',
    'itsdangerous',
    'jaraco.functools',
    'Jinja2',
    'MarkupSafe',
    'more-itertools',
    'Pillow',
    'pycparser',
    'pywin32',
    'qrcode',
    'Werkzeug',
    'PyInstaller',
    'altgraph',
    'packaging',
    'pefile',
    'pyinstaller-hooks-contrib',
    'pywin32-ctypes',
    'setuptools'
)

foreach ($distributionName in $runtimeDistributions) {
    $metadataPath = (& $pythonPath -c "import importlib.metadata as m; print(m.distribution('$distributionName')._path)").Trim()
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Container)) {
        throw "Package metadata was not found: $distributionName"
    }
    $licenseFiles = @(
        Get-ChildItem -LiteralPath $metadataPath -Recurse -File |
            Where-Object {
                $_.Name -match '^(LICENSE|COPYING|NOTICE)(\.|$)' -or
                $_.DirectoryName -match '[\\/]licenses?([\\/]|$)'
            }
    )
    if ($licenseFiles.Count -eq 0) {
        throw "No license file was found for package: $distributionName"
    }

    $destinationRoot = Join-Path $licenseRoot $distributionName
    foreach ($licenseFile in $licenseFiles) {
        $relativePath = $licenseFile.FullName.Substring($metadataPath.Length).TrimStart('\', '/')
        $destinationPath = Join-Path $destinationRoot $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $destinationPath) -Force | Out-Null
        Copy-Item -LiteralPath $licenseFile.FullName -Destination $destinationPath
    }
}

Compress-Archive -LiteralPath $stagePath -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content `
    -LiteralPath "$zipPath.sha256" `
    -Value "$hash  $releaseName.zip" `
    -Encoding ascii

Write-Host "Release created: $zipPath"
Write-Host "SHA-256: $hash"
