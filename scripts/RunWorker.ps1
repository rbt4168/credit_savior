param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath $Root).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Virtual environment missing. Follow doc/installation-guide.md.'
}
Set-Location -LiteralPath $repoRoot
# Python owns child output and restart policy. Native stderr must not become a
# terminating PowerShell error under ErrorActionPreference=Stop.
& $pythonPath (Join-Path $repoRoot 'scripts\supervise_worker.py') --root $repoRoot
exit $LASTEXITCODE
