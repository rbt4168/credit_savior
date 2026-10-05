param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$StartNow
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath $Root).Path
$runnerPath = Join-Path $repoRoot 'scripts\RunWorker.ps1'
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) { throw 'RunWorker.ps1 missing.' }
$taskName = 'NTU-COOL-credit-scammer'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runnerPath + '" -Root "' + $repoRoot + '"'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.Arguments -ne $arguments) {
    throw 'A task with this name belongs to another checkout; leaving it unchanged.'
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
if ($StartNow) { Start-ScheduledTask -TaskName $taskName }
Write-Output ('Installed task: ' + $taskName)
