$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheelhousePath = Join-Path $projectRoot 'wheelhouse'
$manifestPath = Join-Path $projectRoot 'wheelhouse.sha256'

if (-not (Test-Path -LiteralPath $wheelhousePath)) {
    exit 0
}
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw 'wheelhouse.sha256 is missing.'
}

$expected = @{}
foreach ($line in Get-Content -LiteralPath $manifestPath) {
    if ($line -notmatch '^([0-9a-fA-F]{64})  (.+\.whl)$') {
        throw "Invalid wheel hash entry: $line"
    }
    $expected[$Matches[2]] = $Matches[1].ToLowerInvariant()
}

$wheelFiles = @(Get-ChildItem -LiteralPath $wheelhousePath -Filter '*.whl' -File)
if ($wheelFiles.Count -ne $expected.Count) {
    throw 'The wheelhouse file count does not match its hash manifest.'
}

foreach ($wheelFile in $wheelFiles) {
    if (-not $expected.ContainsKey($wheelFile.Name)) {
        throw "Unexpected wheel file: $($wheelFile.Name)"
    }
    $actualHash = (Get-FileHash -LiteralPath $wheelFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expected[$wheelFile.Name]) {
        throw "Wheel hash mismatch: $($wheelFile.Name)"
    }
}

Write-Host 'Dependency wheel hashes verified.'
