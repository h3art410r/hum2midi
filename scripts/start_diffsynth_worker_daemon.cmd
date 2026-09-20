@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_diffsynth_worker_daemon.ps1" %*
