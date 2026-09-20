# GPU Worker Agent Instructions

This directory is the Windows/CUDA DiffSynth-Music worker. Read `docs/DIFFSYNTH_WORKER.md` before deploying or changing its runtime.

- Always pull `origin/main` before restarting the worker.
- Keep `DIFFSYNTH_OFFLOAD_MODE=cpu` unless the task explicitly requests the `dit_cuda` A/B experiment.
- Start with `scripts/start_diffsynth_music_server.ps1`; do not invent a separate command or silently change the offload mode.
- Verify `/health` after startup. It must show the expected `offload_mode`, current `build`, `control+prosody`, both templates, and the RTX 5060 Ti.
- If `dit_cuda` or `none` produces an out-of-memory error, restore `cpu` and report the error; do not hide it or fall back silently.
