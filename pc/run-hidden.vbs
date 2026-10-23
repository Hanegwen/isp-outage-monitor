' Starts steam-report.ps1 without flashing a console window (used by the scheduled task).
Dim dir
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & dir & "\steam-report.ps1""", 0, True
