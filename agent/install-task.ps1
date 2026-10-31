# Run from a Windows PowerShell prompt as the account that owns your models.
# Requires Python on PATH. Task runs at logon and every 30 minutes.
$ErrorActionPreference = "Stop"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $AgentDir "modelharbor_agent.py"
$Config = Join-Path $env:APPDATA "ModelHarbor\agent.json"
if (-not (Test-Path $Config)) { throw "Create $Config from agent/config.example.json first." }
if (-not $env:MODELHARBOR_AGENT_TOKEN) { throw "Set MODELHARBOR_AGENT_TOKEN as a persistent user environment variable first." }
$Python = (Get-Command python -ErrorAction Stop).Source
# Task Scheduler doesn't inherit this PowerShell session's environment.
# The token must be persisted in the user environment (not in task arguments).
$Persisted = [Environment]::GetEnvironmentVariable("MODELHARBOR_AGENT_TOKEN","User")
if (-not $Persisted) { throw "Persist token with [Environment]::SetEnvironmentVariable('MODELHARBOR_AGENT_TOKEN', 'YOUR_SECRET', 'User') and sign out/in." }
$Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $Script + '" --config "' + $Config + '" --once') -WorkingDirectory $AgentDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Trigger.Repetition = New-CimInstance -ClientOnly -ClassName MSFT_TaskRepetitionPattern -Namespace Root/Microsoft/Windows/TaskScheduler -Property @{ Interval = 'PT30M'; Duration = 'P1D'; StopAtDurationEnd = $false }
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ModelHarbor Inventory Sync" -Action $Action -Trigger $Trigger -Principal $Principal -Description "Read-only ComfyUI and Ollama inventory sync" -Force
Write-Host "Installed task: ModelHarbor Inventory Sync"
