# GPU Worker Agent Instructions

This directory is the Windows/CUDA DiffSynth-Music worker. Read `docs/DIFFSYNTH_WORKER.md` before deploying or changing its runtime.

- Always pull `origin/main` before restarting the worker.
- `remote/worker_mode.txt` is the current experiment target. It is currently `dit_cuda`; do not silently replace it with `cpu`.
- Start with `scripts/start_diffsynth_music_server.ps1` without an offload argument so it reads `remote/worker_mode.txt`. An explicit `-OffloadMode cpu` is allowed only as an OOM recovery.
- Verify `/health` after startup. It must show the expected `offload_mode`, current `build`, `control+prosody`, both templates, and the RTX 5060 Ti.
- If `dit_cuda` produces an out-of-memory error, restore `cpu` explicitly and report the error; do not hide it or fall back silently.
