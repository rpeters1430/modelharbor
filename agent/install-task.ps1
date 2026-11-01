# Run from a Windows PowerShell prompt as the account that owns your models.
# Requires Python on PATH. Task runs at logon and every 30 minutes, indefinitely.
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
# A repetition on the logon trigger only starts at the next logon and stops after its duration,
# so repeat from a one-time trigger with no end instead.
$Trigger = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30))
)
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ModelHarbor Inventory Sync" -Action $Action -Trigger $Trigger -Principal $Principal -Description "Read-only ComfyUI and Ollama inventory sync" -Force
Write-Host "Installed task: ModelHarbor Inventory Sync"

# Dedicated per-user hash worker, independent of the 30-minute inventory schedule.
# It only reads locally stored files after an authenticated dashboard request.
$HashAction = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $Script + '" --config "' + $Config + '" --hash-worker') -WorkingDirectory $AgentDir
$HashTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$HashSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "ModelHarbor Hash Worker" -Action $HashAction -Trigger $HashTrigger -Principal $Principal -Settings $HashSettings -Description "On-demand read-only model SHA-256 verification" -Force
Start-ScheduledTask -TaskName "ModelHarbor Hash Worker"
Write-Host "Installed and started task: ModelHarbor Hash Worker"
