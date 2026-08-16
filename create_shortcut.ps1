param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$targetPath = Join-Path $resolvedRoot '.venv\Scripts\pythonw.exe'
$launcherPath = Join-Path $resolvedRoot 'droste_tray.py'
$iconPath = Join-Path $resolvedRoot 'droste.ico'

if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
    throw "Python windowless launcher was not found: $targetPath"
}
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "Droste tray launcher was not found: $launcherPath"
}
if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
    throw "Droste icon was not found: $iconPath"
}

$desktopPath = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktopPath) {
    throw 'The Windows desktop folder could not be located.'
}

$shortcutPath = Join-Path $desktopPath 'Droste.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $targetPath
$shortcut.Arguments = "`"$launcherPath`""
$shortcut.WorkingDirectory = $resolvedRoot
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'Start Droste in the notification area'
$shortcut.WindowStyle = 7
$shortcut.Save()

Write-Host "Created shortcut: $shortcutPath"
