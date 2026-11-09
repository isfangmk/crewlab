# Restart frontend after code/config changes
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\frontend-stop.ps1"
Start-Sleep -Seconds 1
& "$PSScriptRoot\frontend-start.ps1"
