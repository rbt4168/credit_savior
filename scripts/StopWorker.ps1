param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath $Root).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
& $pythonPath -m credit_scammer --root $repoRoot stop
if ($LASTEXITCODE -ne 0) { throw 'Stop request failed.' }
# Give the worker its normal 30-second shutdown grace before forcing the task down.
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $task = Get-ScheduledTask -TaskName 'NTU-COOL-credit-scammer' -ErrorAction SilentlyContinue
    if (-not $task -or $task.State -ne 'Running') { break }
    Start-Sleep -Seconds 1
}
if ($task -and $task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName 'NTU-COOL-credit-scammer'
}
Write-Output 'Stop requested. Data and submission intents are retained.'
