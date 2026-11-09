# Restart backend after code changes (required; --reload is forbidden)
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\backend-stop.ps1"
Start-Sleep -Seconds 1
& "$PSScriptRoot\backend-start.ps1"
