param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = 'Stop'
$expectedSha256 = '67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb'
$resolvedPath = (Resolve-Path -LiteralPath $InstallerPath).Path

$actualSha256 = (Get-FileHash -LiteralPath $resolvedPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw 'Python installer SHA-256 verification failed.'
}

$signature = Get-AuthenticodeSignature -FilePath $resolvedPath
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Python installer signature is not valid: $($signature.Status)"
}
if ($null -eq $signature.SignerCertificate -or
    $signature.SignerCertificate.Subject -notmatch 'CN=Python Software Foundation(?:,|$)') {
    throw 'Python installer signer is not the Python Software Foundation.'
}

Write-Host 'Python installer security check passed.'
