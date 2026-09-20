# DiffSynth-Music Prosody 远端 CUDA 部署

本项目现在提供一个明确的远端 provider：当前开发机负责网页和任务编排，5060Ti 16G 的 Windows 机器负责加载 `DiffSynth-Studio/DiffSynth-Music` 并执行 Prosody Control。输入哼唱先提取 prosody 条件，模型直接生成风格化音频；不会把 MIDI 作为中间层，也不会把原始哼唱轨道混回输出。

## 5060Ti Windows 机器

首次部署时，在仓库根目录 PowerShell 执行常驻守护脚本：

```powershell
.\scripts\run_diffsynth_worker_daemon.ps1 -Port 8765 -PollSeconds 30
```

守护脚本会读取 `remote/worker_mode.txt`，调用底层启动脚本创建 `.venv-diffsynth`、从官方 GitHub checkout DiffSynth-Studio、安装 CUDA 版 PyTorch/torchaudio 和服务依赖，然后启动 `0.0.0.0:8765`。第一次启动会从 ModelScope 下载 `DiffSynth-Studio/DiffSynth-Music`，下载和模型加载可能需要较长时间；守护进程会一直保持运行。之后它每 30 秒 fetch `origin/main`，发现新提交后等待当前任务完成、拉取代码并自动重启 Worker；不需要每次手动部署。需要更快检查时使用 `-PollSeconds 15`。

不要同时运行 `start_diffsynth_music_server.ps1` 和 daemon；底层脚本只用于首次排查或手动恢复。daemon 生命周期和 Worker 输出分别记录在 `remote\logs\worker-daemon.log` 及同目录的时间戳日志中。

如果 CUDA wheel 不是当前机器合适的版本，可以先设置：

```powershell
$env:DIFFSYNTH_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
$env:DIFFSYNTH_REMOTE_TOKEN = "设置一个仅内网使用的随机 token"
```

服务检查：`http://127.0.0.1:8765/health`。不要把未加鉴权的 8765 端口直接暴露到公网；跨机器请使用局域网、VPN 或 SSH 隧道。

## 当前开发机

在 `.env` 中显式选择远端 provider：

```dotenv
H2M_AUDIO_PROVIDER=diffsynth_remote
DIFFSYNTH_REMOTE_URL=http://<5060Ti机器内网IP>:8765
DIFFSYNTH_REMOTE_TIMEOUT_SECONDS=900
DIFFSYNTH_REMOTE_TOKEN=与服务端相同的token
```

重启当前 FastAPI 服务后，`GET /api/health` 会显示 `runtime: remote-cuda` 和远端设备信息。远端不可用时任务会明确失败并写入后端日志；不会静默回退到 Stable Audio。

## 当前实现边界

- 当前 worker 加载 `template_control` 和 `template_prosody`，提供 Control + Prosody 联合条件以及两档 input-audio 锚定；`template_reference` 暂不加载。Windows 16GB 机器使用 CPU/offload 配置，启动时必须观察显存峰值。
- worker 只加载 `template_prosody`，不加载 `template_control` 和 `template_reference`；Prosody 模板常驻显存，主模型使用官方 low-VRAM 磁盘 offload 配置，以适配 16GB 显存的 RTX 5060 Ti。
- 五个前端方案会依次调用同一个远端 worker，因此首次实验会消耗较长时间；可通过 `DIFFSYNTH_STEPS` 和 `DIFFSYNTH_CFG_SCALE` 调整质量/速度。
- 5060Ti 16G 是否能在目标时长和步数下稳定运行，需要在目标机实测；服务启动时会检查 CUDA，显存峰值会打印到 worker 日志。
