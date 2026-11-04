<#
.SYNOPSIS
  Runs NextPush at every Windows start, with no one logged in (a Scheduled Task as SYSTEM), and restarts it if it stops. Needs an elevated PowerShell.

  powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Program C:\NextPush\nextpush.exe
  powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Remove

  The data lives in C:\ProgramData\NextPush. The shared folders must be readable (and writable, for users who may delete after copying) by the SYSTEM account.
  The setup code is in C:\ProgramData\NextPush\setup-code.txt until the first setup is done. Add a Windows Firewall rule for the ports you use (8095 on the LAN, 8096 for the proxy).
#>
param(
  [string]$Program = (Join-Path (Split-Path -Parent $PSScriptRoot) "nextpush.exe"),
  [switch]$Remove
)
$ErrorActionPreference = "Stop"
$name = "NextPush"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this from an elevated (administrator) PowerShell." }
if ($Remove) {
  Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Removed the $name task (the data in C:\ProgramData\NextPush was kept)."
  return
}
if (-not (Test-Path -LiteralPath $Program)) { throw "nextpush.exe not found at $Program" }
$data = Join-Path $env:ProgramData "NextPush"
New-Item -ItemType Directory -Force -Path $data | Out-Null
$action = New-ScheduledTaskAction -Execute $Program -Argument "--data-dir `"$data`"" -WorkingDirectory (Split-Path -Parent $Program)
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "NextPush will start with Windows. Data: $data"
Start-Sleep -Seconds 4
$code = Join-Path $data "setup-code.txt"
if (Test-Path -LiteralPath $code) { Write-Host "First-time setup code: $((Get-Content -LiteralPath $code -Raw).Trim())   (open http://localhost:8095/)" }
