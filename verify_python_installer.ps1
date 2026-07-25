param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = 'Stop'
$expectedSha256 = 'c54d9b9bbb8a36e6489363ddd01139707fd781d72f1f9e90c7ec65d0061368e0'
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
