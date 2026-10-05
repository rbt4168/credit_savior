param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath $Root).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$runnerPath = Join-Path $repoRoot 'scripts\RunWorker.ps1'
$expectedArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runnerPath + '" -Root "' + $repoRoot + '"'
$ownedTasks = @()
foreach ($taskName in @('NTU-COOL-credit-savior','NTU-COOL-credit-scammer')) {
    $candidate = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.Actions.Arguments -eq $expectedArguments) {
        $ownedTasks += $candidate.TaskName
    }
}
& $pythonPath -m credit_savior --root $repoRoot stop
if ($LASTEXITCODE -ne 0) { throw 'Stop request failed.' }
# Give the worker its normal 30-second shutdown grace before forcing the task down.
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $runningTasks = @($ownedTasks | ForEach-Object {
        Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
    } | Where-Object State -eq 'Running')
    if (-not $runningTasks.Count) { break }
    Start-Sleep -Seconds 1
}
foreach ($taskName in $ownedTasks) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $taskName }
}
Write-Output 'Stop requested. Data and submission intents are retained.'
