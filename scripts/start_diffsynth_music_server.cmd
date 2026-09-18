@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_diffsynth_music_server.ps1" %*
