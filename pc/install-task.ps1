# Run once on the PC (no admin needed): sends the Steam summary to the Pi every 5 minutes while signed in.
$vbs = Join-Path $PSScriptRoot "run-hidden.vbs"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Comcast monitor - Steam report" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Copies Steam's download log summary to the Pi data-usage monitor" -Force
