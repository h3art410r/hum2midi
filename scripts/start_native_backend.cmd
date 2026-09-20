@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_native_backend.ps1" %*
