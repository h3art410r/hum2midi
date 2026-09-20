# DiffSynth Worker Agent 运行说明

这份说明是给负责 GPU Worker 部署的 Agent 读取的。Worker 运行在 Windows + RTX 5060 Ti 16GB，开发机 FastAPI 通过 `http://192.168.9.100:8765` 调用它。Worker 只负责加载模型和生成音频，不修改前端、后端任务逻辑或提示词。

## 当前任务目标

当前目标是直接测量 DiT 常驻显存是否能提速，**当前模式必须是 `dit_cuda`，不需要重复 `cpu` 基线**。仓库文件 `remote/worker_mode.txt` 是当前实验目标，启动脚本不带参数时会自动读取它。

只有 `dit_cuda` 启动或推理发生 OOM，才允许临时切回 `cpu`，并把 OOM 原文和发生阶段报告给主 Agent。

## 常驻启动和自动更新

Worker 现在由守护脚本负责生命周期。首次部署时先停止手动启动的 raw worker，再在仓库根目录启动：

```powershell
.\scripts\run_diffsynth_worker_daemon.ps1 -Port 8765 -PollSeconds 30
```

脚本会读取 `remote/worker_mode.txt`，启动现有的模型启动脚本，并保持 Worker 常驻。之后每 30 秒执行一次 `git fetch origin main`：发现新提交后会等待当前生成完成，执行 fast-forward，重新安装必要依赖并重启模型，最后轮询 `/health` 直到新的 `build` 可用。可以把 `-PollSeconds` 改为 `15` 加快更新检查。

守护进程日志位于 `remote\logs\worker-daemon.log`；每次 Worker 启动的 stdout/stderr 位于同目录的带时间戳文件。日志目录已加入 `.gitignore`，不会进入提交。

守护脚本的安全行为：工作区有未提交改动时拒绝自动拉取；本地分支与远端分叉时拒绝 reset；更新前等待正在生成的请求；新版本启动健康检查失败时自动回滚到更新前的 clean commit。守护脚本自身变更后需要手动重启一次守护进程，Worker 代码和 `worker_mode.txt` 的后续提交不需要手动重启。

首次启动或调试时仍可直接运行底层脚本：

```powershell
.\scripts\start_diffsynth_music_server.ps1 -Port 8765
```

但不要让它和守护脚本同时监听 8765。`dit_cuda` 是实验模式，16GB 显存可能 OOM；发生 OOM 时应修改 `remote/worker_mode.txt` 为 `cpu` 并提交，让守护脚本自动切换，然后报告原始错误。`none` 会完全关闭 VRAM 管理，只能在明确要求时使用。

## 启动后验收

启动后必须检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health | ConvertTo-Json
```

返回中必须能看到：

- `status: ok`
- `offload_mode` 与本次启动参数一致
- `build` 是刚刚 `git pull` 后的提交短 hash
- `control: control+prosody`
- `templates: control, prosody`
- `device` 是 RTX 5060 Ti

如果 `/health` 没有 `offload_mode` 或 `build`，说明运行的仍是旧代码，不能继续做性能结论。

## 性能日志

生成完成后可读取：

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/debug/logs?since=0&limit=500" | ConvertTo-Json -Depth 12
```

日志包含模板正负分支、Pipeline Unit、模型设备切换、每个 DiT 正负 CFG forward、每个 denoise step、VAE 解码、保存和显存峰值。不要把音频内容、密钥或完整 prompt 写入 Worker 日志。
