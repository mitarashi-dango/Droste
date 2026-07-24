#Requires -RunAsAdministrator

param(
    [ValidateRange(1, 65535)]
    [int]$Port = 5443
)

$ErrorActionPreference = 'Stop'
$ruleName = "Room Indicator HTTPS $Port (Private LAN)"
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
    Select-Object -First 1
$pythonPath = (Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)").ExecutablePath
$localAddress = $listener.LocalAddress

if (-not $pythonPath) {
    throw "Room Indicator HTTPS server process was not found on port $Port."
}

$legacyRuleNames = @(
    'Room Indicator HTTPS (Local Wi-Fi)',
    "Room Indicator HTTPS $Port (Local Wi-Fi)"
)
foreach ($legacyRuleName in $legacyRuleNames) {
    Get-NetFirewallRule -DisplayName $legacyRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
}

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Remove-NetFirewallRule
}

try {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description 'Allow Room Indicator HTTPS only on a private local network.' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -LocalAddress $localAddress `
        -Program $pythonPath `
        -Profile Private `
        -RemoteAddress LocalSubnet | Out-Null
    Write-Host "Created firewall rule: $ruleName"
    Write-Host "Local address: $localAddress"
} catch {
    throw "Failed to create the private-network firewall rule: $($_.Exception.Message)"
}
