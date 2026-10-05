param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath $Root).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Virtual environment missing. Follow README installation instructions.'
}
Set-Location -LiteralPath $repoRoot
$retryDelays = @(15, 60, 300)
$retryCount = 0
$logDirectory = Join-Path $repoRoot 'data\logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$consolePath = Join-Path $logDirectory 'supervisor.log'
while ($true) {
    # Credentials are loaded by Python, never interpolated into process arguments.
    & $pythonPath -m credit_scammer --root $repoRoot run >> $consolePath 2>&1
    $workerCode = $LASTEXITCODE
    if ($workerCode -ne 30 -or $retryCount -ge $retryDelays.Count) { exit $workerCode }
    Start-Sleep -Seconds $retryDelays[$retryCount]
    $retryCount++
}
