#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'
$rules = @(
    Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Group -eq 'Droste' -or
            $_.DisplayName -like 'Droste HTTPS * (Private LAN)'
        }
)

if ($rules.Count -eq 0) {
    Write-Host 'No Droste firewall rules were found.'
    exit 0
}

$rules | Remove-NetFirewallRule
Write-Host "Removed $($rules.Count) Droste firewall rule(s)."
