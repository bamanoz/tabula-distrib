# Install gateway-telegram as a Windows Task Scheduler service.
# Usage: powershell -ExecutionPolicy Bypass -File install-service.ps1

$ErrorActionPreference = "Stop"

$TabulaHome = if ($env:TABULA_HOME) { $env:TABULA_HOME } else { "$env:USERPROFILE\.tabula" }
$VenvPython = "$TabulaHome\.venv\Scripts\python3.exe"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript  = "$ScriptDir\run.py"
$TaskName   = "TabulaGatewayTelegram"
$LogDir     = "$TabulaHome\logs"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Python venv not found at $VenvPython. Run install.ps1 first."
    exit 1
}

if (-not (Test-Path $RunScript)) {
    Write-Error "run.py not found at $RunScript"
    exit 1
}

# Create log directory
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the task
$action = New-ScheduledTaskAction `
    -Execute $VenvPython `
    -Argument "`"$RunScript`"" `
    -WorkingDirectory $TabulaHome

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Tabula Telegram Gateway" | Out-Null

# Start it now
Start-ScheduledTask -TaskName $TaskName

Write-Host "`ngateway-telegram service installed (Task Scheduler)" -ForegroundColor Green
Write-Host "`nCheck status:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Get-Content $LogDir\gateway-telegram*.log -Wait"
