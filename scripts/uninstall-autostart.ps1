# Stop starting Market Lens at sign-in (undoes install-autostart.ps1). A copy running now keeps running
# until you sign out or end "pythonw.exe" in Task Manager.
# "PatternCharts" is the app's old name; it's removed too so an install from before the rename doesn't linger.
foreach ($name in "MarketLens", "PatternCharts") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed scheduled task '$name'."
    }
    $lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "$name.lnk"
    if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Removed Startup shortcut $name.lnk." }
}
