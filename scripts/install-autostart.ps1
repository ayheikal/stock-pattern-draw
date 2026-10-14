# Start Market Lens in the background every time you sign in to Windows,
# so Telegram alerts and the phone app work without a terminal window.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1            # with phone access
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -NoPhone   # this computer only
#
# Undo with scripts\uninstall-autostart.ps1. Logs: data\server.log
param([switch]$NoPhone, [int]$Port = 8000, [switch]$StartNow)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"   # pythonw = no console window
if (-not (Test-Path $pyw)) { throw "Python environment not found at $pyw. Create it first (see README)." }
$arguments = "-m app.serve --port $Port" + $(if ($NoPhone) { "" } else { " --lan" })
$name = "MarketLens"

try {
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $arguments -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force `
        -Description "Market Lens web app (alerts + phone access). Installed by scripts\install-autostart.ps1" | Out-Null
    Write-Host "Installed: scheduled task '$name' starts the app at sign-in ($arguments)."
    if ($StartNow) { Start-ScheduledTask -TaskName $name; Write-Host "Started." }
}
catch {
    # No permission for scheduled tasks: fall back to a shortcut in the Startup folder.
    $lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "$name.lnk"
    $sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sh.TargetPath = $pyw; $sh.Arguments = $arguments; $sh.WorkingDirectory = $root; $sh.WindowStyle = 7
    $sh.Save()
    Write-Host "Installed: Startup shortcut $lnk ($arguments). (Scheduled task not allowed: $($_.Exception.Message))"
    if ($StartNow) { Start-Process -FilePath $pyw -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden; Write-Host "Started." }
}
if (-not $NoPhone) {
    Write-Host "Phone access is on. The first time it starts, Windows may ask to allow Python on the network: choose 'Private networks' and Allow."
}
