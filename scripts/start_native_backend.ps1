[CmdletBinding()]
param(
  [int]$Port = 8000,
  [string]$Python = $env:H2M_PYTHON
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Python) {
  $candidate = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  $Python = if (Test-Path $candidate) { $candidate } else { "python" }
}
$env:PYTHONPATH = $RepoRoot
$env:NATIVE_BACKEND_PORT = $Port
Write-Host "Starting Hum2Midi native backend on http://127.0.0.1:$Port"
& $Python -m uvicorn native.api:app --host 0.0.0.0 --port $Port
