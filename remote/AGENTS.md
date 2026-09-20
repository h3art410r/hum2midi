# GPU Worker Agent Instructions

This directory is the Windows/CUDA DiffSynth-Music worker. Read `docs/DIFFSYNTH_WORKER.md` before deploying or changing its runtime.

## One-time deployment

- Pull `origin/main` before the first deployment. Do not start the raw worker and the daemon at the same time.
- `remote/worker_mode.txt` is the current experiment target. It is currently `cpu`, the validated 16GB-safe path; do not silently switch it to `dit_cuda`.
- Stop any manually launched `start_diffsynth_music_server.ps1` process, then start the resident daemon once:

  ```powershell
  .\scripts\run_diffsynth_worker_daemon.ps1 -Port 8765 -PollSeconds 30
  ```

  The daemon owns the worker process, keeps it alive, polls `origin/main` every 30 seconds, drains active requests, fast-forwards the checkout, and restarts the model automatically when code or configuration changes. Use `-PollSeconds 15` when a faster update loop is useful. Do not manually restart the model for every commit after the daemon is running.
- The daemon writes its lifecycle log to `remote\logs\worker-daemon.log` and each worker launch to timestamped `remote\logs\worker-*.stdout.log` / `worker-*.stderr.log` files. These logs are ignored by Git.
- To detach it from the current shell while keeping it resident, use:

  ```powershell
  Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', "$PWD\scripts\run_diffsynth_worker_daemon.ps1",
    '-Port', '8765', '-PollSeconds', '30'
  )
  ```

- The daemon refuses to overwrite a dirty checkout, refuses a divergent Git history, waits for active generation requests before an update, and rolls back to the previous clean commit if the new worker never becomes healthy. A daemon restart is required only when the daemon script itself changes.

## Runtime and recovery

- Verify `/health` after the first startup and after an automatic redeploy. It must show the expected `offload_mode`, current `build`, `control+prosody`, both templates, and the RTX 5060 Ti.
- Keep the CPU template paging and `torch.no_grad()` path enabled. If `dit_cuda` is used for an explicit A/B test and OOMs, restore `cpu` in `remote/worker_mode.txt` and report the original phase/logs; do not hide the failure or silently fall back.
- If the daemon is stopped, do not start the raw worker alongside a stale daemon. Stop the old daemon process first, then start the daemon again.
