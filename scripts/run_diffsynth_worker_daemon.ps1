[CmdletBinding()]
param(
  [ValidateSet(15, 30)]
  [int]$PollSeconds = 30,
  [int]$Port = 8765,
  [int]$StartupTimeoutSeconds = 900,
  [int]$DrainTimeoutSeconds = 300,
  [string]$Branch = "main",
  [switch]$NoInitialPull
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$StartScript = Join-Path $RepoRoot "scripts\start_diffsynth_music_server.ps1"
$LogDir = Join-Path $RepoRoot "remote\logs"
$DaemonLog = Join-Path $LogDir "worker-daemon.log"
$WorkerUrl = "http://127.0.0.1:$Port"
$script:WorkerProcess = $null
$script:CurrentHead = ""
$script:LastBusySignature = ""

New-Item -ItemType Directory -Force $LogDir | Out-Null

function Write-DaemonLog {
  param([Parameter(Mandatory)][string]$Message)
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  Add-Content -LiteralPath $DaemonLog -Value $line -Encoding UTF8
  Write-Host $line
}

function Invoke-GitText {
  param([Parameter(Mandatory)][string[]]$Arguments)
  $output = & git -C $RepoRoot @Arguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "git $($Arguments -join ' ') failed: $($output -join ' ')"
  }
  return ($output -join "`n").Trim()
}

function Get-CurrentHead {
  return (Invoke-GitText @("rev-parse", "HEAD"))
}

function Test-WorkingTreeClean {
  $status = Invoke-GitText @("status", "--porcelain=v1")
  return [string]::IsNullOrWhiteSpace($status)
}

function Get-RemoteHead {
  Invoke-GitText @("fetch", "--quiet", "origin", $Branch) | Out-Null
  return (Invoke-GitText @("rev-parse", "origin/$Branch"))
}

function Test-WorkerHealthy {
  try {
    $health = Invoke-RestMethod -Uri "$WorkerUrl/health" -TimeoutSec 5
    return ($health.status -eq "ok")
  } catch {
    return $false
  }
}

function Get-ActiveRequestIds {
  try {
    $payload = Invoke-RestMethod -Uri "$WorkerUrl/debug/logs?limit=500" -TimeoutSec 5
    $terminalEvents = @("AUDIO_SAVE_DONE", "REQUEST_ERROR", "REQUEST_CANCELLED")
    $active = @()
    foreach ($group in ($payload.logs | Group-Object -Property request_id)) {
      if ([string]::IsNullOrWhiteSpace([string]$group.Name)) { continue }
      $last = $group.Group | Sort-Object { [int]$_.seq } | Select-Object -Last 1
      if ($terminalEvents -notcontains [string]$last.event) {
        $active += [string]$group.Name
      }
    }
    return ,$active
  } catch {
    # A missing debug response means the worker is starting or is unhealthy;
    # do not kill a process while its state cannot be observed.
    return $null
  }
}

function Wait-WorkerIdle {
  $deadline = (Get-Date).AddSeconds($DrainTimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $active = Get-ActiveRequestIds
    if ($null -eq $active) {
      Write-DaemonLog "cannot inspect active requests; waiting before restart"
      Start-Sleep -Seconds 5
      continue
    }
    if ($active.Count -eq 0) {
      Write-DaemonLog "worker is idle; restart is safe"
      return $true
    }
    $signature = $active -join ","
    if ($signature -ne $script:LastBusySignature) {
      Write-DaemonLog "waiting for active request(s) to finish: $signature"
      $script:LastBusySignature = $signature
    }
    Start-Sleep -Seconds 5
  }
  Write-DaemonLog "drain timeout reached after ${DrainTimeoutSeconds}s; stopping worker"
  return $false
}

function Stop-OwnedWorker {
  if ($null -eq $script:WorkerProcess) { return }
  try {
    $script:WorkerProcess.Refresh()
    if (-not $script:WorkerProcess.HasExited) {
      Write-DaemonLog "stopping worker process pid=$($script:WorkerProcess.Id)"
      # The launcher is PowerShell and uvicorn is its child. Kill the process
      # tree so an old Python worker cannot keep port 8765 after a redeploy.
      & taskkill.exe /PID $script:WorkerProcess.Id /T /F 2>$null | Out-Null
      try { Wait-Process -Id $script:WorkerProcess.Id -Timeout 30 -ErrorAction SilentlyContinue } catch {}
    }
  } catch {
    Write-DaemonLog "worker stop warning: $($_.Exception.Message)"
  } finally {
    $script:WorkerProcess = $null
  }
}

function Start-OwnedWorker {
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $stdoutPath = Join-Path $LogDir "worker-$stamp.stdout.log"
  $stderrPath = Join-Path $LogDir "worker-$stamp.stderr.log"
  $arguments = @(
    "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", $StartScript, "-Port", $Port.ToString()
  )
  Write-DaemonLog "starting worker from $StartScript; stdout=$stdoutPath; stderr=$stderrPath"
  $script:WorkerProcess = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList $arguments `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

  $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $script:WorkerProcess.Refresh()
    if ($script:WorkerProcess.HasExited) {
      throw "worker launcher exited with code $($script:WorkerProcess.ExitCode); see $stdoutPath and $stderrPath"
    }
    if (Test-WorkerHealthy) {
      $health = Invoke-RestMethod -Uri "$WorkerUrl/health" -TimeoutSec 5
      Write-DaemonLog "worker healthy build=$($health.build) execution=$($health.execution) device=$($health.device)"
      return
    }
    Start-Sleep -Seconds 5
  }
  throw "worker did not become healthy within ${StartupTimeoutSeconds}s; see $stdoutPath and $stderrPath"
}

function FastForwardRepository {
  if (-not (Test-WorkingTreeClean)) {
    Write-DaemonLog "repository has local changes; refusing automatic pull"
    return $false
  }
  $before = Get-CurrentHead
  $remote = Get-RemoteHead
  if ($remote -eq $before) {
    return $false
  }
  $ancestorOutput = & git -C $RepoRoot merge-base --is-ancestor $before $remote 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "local HEAD $before and origin/$Branch $remote diverged; refusing automatic reset"
  }
  Invoke-GitText @("merge", "--ff-only", "origin/$Branch") | Out-Null
  $after = Get-CurrentHead
  Write-DaemonLog "repository fast-forwarded $before -> $after"
  return $true
}

function Update-WorkerIfNeeded {
  if (-not (Test-WorkingTreeClean)) {
    Write-DaemonLog "update check skipped because the repository is dirty"
    return
  }
  $before = Get-CurrentHead
  $remote = Get-RemoteHead
  if ($remote -eq $before) { return }
  Write-DaemonLog "new origin/$Branch detected: $before -> $remote"
  [void](Wait-WorkerIdle)

  # Fetch again after draining so a commit pushed while the model was busy is
  # included in the restart.
  if (-not (Test-WorkingTreeClean)) {
    Write-DaemonLog "update cancelled because the repository became dirty"
    return
  }
  $remote = Get-RemoteHead
  if ($remote -eq $before) { return }

  Stop-OwnedWorker
  try {
    $ancestorOutput = & git -C $RepoRoot merge-base --is-ancestor $before $remote 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "local HEAD $before and origin/$Branch $remote diverged; refusing automatic reset"
    }
    Invoke-GitText @("merge", "--ff-only", "origin/$Branch") | Out-Null
    $script:CurrentHead = Get-CurrentHead
    Start-OwnedWorker
    Write-DaemonLog "worker redeployed at build=$script:CurrentHead"
  } catch {
    Write-DaemonLog "redeploy failed: $($_.Exception.Message)"
    # The tree was clean before the update, so rollback is safe and keeps the
    # endpoint available while the broken commit is investigated.
    try {
      Invoke-GitText @("reset", "--hard", $before) | Out-Null
      $script:CurrentHead = Get-CurrentHead
      Start-OwnedWorker
      Write-DaemonLog "rolled back to build=$script:CurrentHead"
    } catch {
      Write-DaemonLog "rollback failed: $($_.Exception.Message)"
      throw
    }
  }
}

try {
  if (-not (Test-Path $StartScript)) {
    throw "worker start script not found: $StartScript"
  }
  if (Test-WorkerHealthy) {
    throw "port $Port is already serving a worker; stop the manually launched worker before starting the daemon"
  }
  if (-not $NoInitialPull) {
    [void](FastForwardRepository)
  }
  $script:CurrentHead = Get-CurrentHead
  Start-OwnedWorker
  Write-DaemonLog "daemon started; polling origin/$Branch every ${PollSeconds}s"
  while ($true) {
    try {
      $script:WorkerProcess.Refresh()
      if ($script:WorkerProcess.HasExited) {
        Write-DaemonLog "worker process exited with code $($script:WorkerProcess.ExitCode); restarting"
        $script:WorkerProcess = $null
        Start-OwnedWorker
      }
      Update-WorkerIfNeeded
    } catch {
      Write-DaemonLog "poll/restart error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $PollSeconds
  }
} catch {
  Write-DaemonLog "daemon stopped: $($_.Exception.Message)"
  exit 1
} finally {
  Stop-OwnedWorker
}
