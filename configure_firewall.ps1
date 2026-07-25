#Requires -RunAsAdministrator

param(
    [ValidateRange(1, 65535)]
    [int]$Port = 5443
)

$ErrorActionPreference = 'Stop'
$ruleName = "Droste HTTPS $Port (Private LAN)"
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
    Select-Object -First 1
$pythonPath = (Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)").ExecutablePath
$localAddress = $listener.LocalAddress

if (-not $pythonPath) {
    throw "Droste HTTPS server process was not found on port $Port."
}

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Remove-NetFirewallRule
}

try {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description 'Allow Droste HTTPS only on a private local network.' `
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
