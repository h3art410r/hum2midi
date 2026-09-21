[CmdletBinding()]
param(
  [int]$Port = 8765,
  [string]$Python = $env:DIFFSYNTH_PYTHON
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Python) { $Python = "python" }
$MemoryProfileFile = Join-Path $RepoRoot "remote\worker_memory_profile.txt"
if (-not $env:DIFFSYNTH_MEMORY_PROFILE -and (Test-Path $MemoryProfileFile)) {
  $env:DIFFSYNTH_MEMORY_PROFILE = (Get-Content $MemoryProfileFile -Raw).Trim()
}
if (-not $env:DIFFSYNTH_MEMORY_PROFILE) { $env:DIFFSYNTH_MEMORY_PROFILE = "official" }
if (@("official", "resident_prosody") -notcontains $env:DIFFSYNTH_MEMORY_PROFILE) {
  throw "Invalid DIFFSYNTH_MEMORY_PROFILE '$($env:DIFFSYNTH_MEMORY_PROFILE)'. Use official or resident_prosody."
}
$Venv = Join-Path $RepoRoot ".venv-diffsynth"
$DiffSynthDir = Join-Path $RepoRoot ".vendor\DiffSynth-Studio"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
  & $Python -m venv $Venv
}
$Vpy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path (Join-Path $DiffSynthDir ".git"))) {
  New-Item -ItemType Directory -Force (Split-Path $DiffSynthDir) | Out-Null
  git clone https://github.com/modelscope/DiffSynth-Studio.git $DiffSynthDir
}

& $Vpy -m pip install --upgrade pip
$TorchIndex = if ($env:DIFFSYNTH_TORCH_INDEX_URL) { $env:DIFFSYNTH_TORCH_INDEX_URL } else { "https://download.pytorch.org/whl/cu128" }
& $Vpy -m pip install torch torchaudio --index-url $TorchIndex
& $Vpy -m pip install -r (Join-Path $RepoRoot "remote\requirements.txt")
& $Vpy -m pip install -e $DiffSynthDir

$env:PYTHONPATH = $RepoRoot
$env:DIFFSYNTH_SERVER_PORT = $Port
$env:H2M_WORKER_BUILD = (& git -C $RepoRoot rev-parse --short HEAD 2>$null).Trim()
if (-not $env:H2M_WORKER_BUILD) { $env:H2M_WORKER_BUILD = "unknown" }
$env:DIFFSYNTH_SERVER_HOST = if ($env:DIFFSYNTH_SERVER_HOST) { $env:DIFFSYNTH_SERVER_HOST } else { "0.0.0.0" }

Write-Host "Starting native DiffSynth-Music Prosody worker on $($env:DIFFSYNTH_SERVER_HOST):$Port"
Write-Host "Build: $($env:H2M_WORKER_BUILD); execution: official Prosody Quick Start; memory profile: $($env:DIFFSYNTH_MEMORY_PROFILE)"
$ModelId = if ($env:DIFFSYNTH_MODEL_ID) { $env:DIFFSYNTH_MODEL_ID } else { "DiffSynth-Studio/DiffSynth-Music" }
Write-Host "Model: $ModelId"
& $Vpy -m uvicorn native.worker_server:app --host $env:DIFFSYNTH_SERVER_HOST --port $Port
