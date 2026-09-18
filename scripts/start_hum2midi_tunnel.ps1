$ErrorActionPreference = 'Continue'
$workspace = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $workspace 'data\demo'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'hum2midi-tunnel.log'

while ($true) {
    Add-Content -Path $log -Value "[$(Get-Date -Format s)] connecting reverse tunnel"
    & ssh.exe -N -T `
        -o ExitOnForwardFailure=yes `
        -o ServerAliveInterval=30 `
        -o ServerAliveCountMax=3 `
        -R '127.0.0.1:18000:127.0.0.1:8000' `
        ubuntu@kr.sunyongfei.cn *>> $log
    Add-Content -Path $log -Value "[$(Get-Date -Format s)] tunnel exited with code $LASTEXITCODE; retrying in 5s"
    Start-Sleep -Seconds 5
}
