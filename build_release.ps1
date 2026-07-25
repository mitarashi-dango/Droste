param(
    [switch]$WithoutWheelhouse
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Get-Content -LiteralPath (Join-Path $projectRoot 'VERSION') -Raw).Trim()
$distRoot = Join-Path $projectRoot 'dist'
$releaseName = "Droste-$version-windows-x64"
$stagePath = Join-Path $distRoot $releaseName
$zipPath = Join-Path $distRoot "$releaseName.zip"
$pythonVersion = '3.13.14'
$pythonInstallerName = "python-$pythonVersion-amd64.exe"
$pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonVersion/$pythonInstallerName"
$pythonInstallerSha256 = 'c54d9b9bbb8a36e6489363ddd01139707fd781d72f1f9e90c7ec65d0061368e0'
$pythonLicenseUrl = 'https://raw.githubusercontent.com/python/cpython/v3.13.14/LICENSE'
$pythonLicenseSha256 = '78b12c3a81360b357002334f0e70ea0e92eebf7a9b358805c03c48484945f3bb'
$vendorPath = Join-Path $projectRoot 'vendor'
$pythonInstallerPath = Join-Path $vendorPath $pythonInstallerName
$pythonLicensePath = Join-Path $vendorPath 'PYTHON-LICENSE.txt'

$resolvedDistRoot = [System.IO.Path]::GetFullPath($distRoot)
$resolvedStagePath = [System.IO.Path]::GetFullPath($stagePath)
if (-not $resolvedStagePath.StartsWith(
    $resolvedDistRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw 'Unsafe release staging path.'
}

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
if (Test-Path -LiteralPath $stagePath) {
    Remove-Item -LiteralPath $stagePath -Recurse -Force
}
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
New-Item -ItemType Directory -Path $stagePath | Out-Null

$releaseFiles = @(
    'VERSION',
    'README.md',
    'START-HERE.txt',
    'app.py',
    'tls_utils.py',
    'requirements.txt',
    'requirements.lock.txt',
    'setup.bat',
    'regain.bat',
    'droste_tray.pyw',
    'create_shortcut.ps1',
    'droste.ico',
    'configure_firewall.ps1',
    'verify_wheelhouse.ps1',
    'verify_python_installer.ps1'
)
foreach ($relativePath in $releaseFiles) {
    Copy-Item `
        -LiteralPath (Join-Path $projectRoot $relativePath) `
        -Destination (Join-Path $stagePath $relativePath)
}
Copy-Item `
    -LiteralPath (Join-Path $projectRoot 'static') `
    -Destination (Join-Path $stagePath 'static') `
    -Recurse

New-Item -ItemType Directory -Path $vendorPath -Force | Out-Null
if (-not (Test-Path -LiteralPath $pythonInstallerPath)) {
    Write-Host "Downloading official Python $pythonVersion 64-bit installer..."
    Invoke-WebRequest `
        -UseBasicParsing `
        -Uri $pythonInstallerUrl `
        -OutFile $pythonInstallerPath
}
$actualInstallerSha256 = (
    Get-FileHash -LiteralPath $pythonInstallerPath -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($actualInstallerSha256 -ne $pythonInstallerSha256) {
    throw 'Python installer SHA-256 verification failed.'
}
$installerSignature = Get-AuthenticodeSignature -FilePath $pythonInstallerPath
if ($installerSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
    $null -eq $installerSignature.SignerCertificate -or
    $installerSignature.SignerCertificate.Subject -notmatch 'CN=Python Software Foundation(?:,|$)') {
    throw 'Python installer Authenticode verification failed.'
}

if (-not (Test-Path -LiteralPath $pythonLicensePath)) {
    Write-Host 'Downloading the Python license...'
    Invoke-WebRequest `
        -UseBasicParsing `
        -Uri $pythonLicenseUrl `
        -OutFile $pythonLicensePath
}
$actualLicenseSha256 = (
    Get-FileHash -LiteralPath $pythonLicensePath -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($actualLicenseSha256 -ne $pythonLicenseSha256) {
    throw 'Python license SHA-256 verification failed.'
}

Copy-Item -LiteralPath $pythonInstallerPath -Destination (Join-Path $stagePath $pythonInstallerName)
Copy-Item -LiteralPath $pythonLicensePath -Destination (Join-Path $stagePath 'PYTHON-LICENSE.txt')

if (-not $WithoutWheelhouse) {
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Run setup.bat before building a release.'
    }
    $wheelhousePath = Join-Path $stagePath 'wheelhouse'
    New-Item -ItemType Directory -Path $wheelhousePath | Out-Null
    & $pythonPath -m pip download `
        --only-binary=:all: `
        --platform win_amd64 `
        --implementation cp `
        --python-version 313 `
        --abi cp313 `
        --dest $wheelhousePath `
        -r (Join-Path $projectRoot 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to build the dependency wheelhouse.'
    }
    $wheelHashes = Get-ChildItem -LiteralPath $wheelhousePath -Filter '*.whl' -File |
        Sort-Object Name |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $($_.Name)"
        }
    Set-Content `
        -LiteralPath (Join-Path $stagePath 'wheelhouse.sha256') `
        -Value $wheelHashes `
        -Encoding ascii
}

Compress-Archive -LiteralPath $stagePath -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content `
    -LiteralPath "$zipPath.sha256" `
    -Value "$hash  $releaseName.zip" `
    -Encoding ascii

Write-Host "Release created: $zipPath"
Write-Host "SHA-256: $hash"
