# DiffSynth-Music 远端 CUDA 部署

开发机运行 FastAPI 和网页，5060 Ti Windows 机器运行常驻 DiffSynth-Music Worker。输入哼唱按官方 Prosody 条件准备，再由官方 `TemplatePipeline` 直接生成完整音频；不使用 MIDI 中间层，也不把原始人声轨混回输出。

## GPU Worker

首次在仓库根目录运行：

```powershell
.\scripts\run_diffsynth_worker_daemon.ps1 -Port 8765 -PollSeconds 30
```

daemon 会创建 `.venv-diffsynth`、安装 CUDA 版 PyTorch 和 `remote/requirements.txt`、安装本地 DiffSynth-Studio checkout，并启动 `0.0.0.0:8765`。首次运行会下载 `DiffSynth-Studio/DiffSynth-Music` 权重。后续每 30 秒检查 `origin/main`，自动等待请求结束并部署新提交。不要同时运行 raw Worker 和 daemon。

Worker 的执行方式固定为官方模型卡路径，配置文件 `remote/worker_mode.txt` 必须为 `official`。官方低显存配置和模板懒加载由 Worker 代码直接声明，不再有自定义 offload/cache/denoise 分支。

检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health | ConvertTo-Json
```

应看到 `status=ok`、`execution=official_model_card`、RTX 5060 Ti 设备名和当前 `build`。

## 开发机

`.env` 中选择远端 provider：

```dotenv
H2M_AUDIO_PROVIDER=diffsynth_remote
DIFFSYNTH_REMOTE_URL=http://<5060Ti机器内网IP>:8765
DIFFSYNTH_REMOTE_TIMEOUT_SECONDS=900
DIFFSYNTH_REMOTE_TOKEN=与服务端相同的token
```

重启 FastAPI 后，`GET /api/health` 会报告远端 Worker 健康状态。远端不可用或官方模型失败时，任务明确失败，不静默回退到 Stable Audio。

## 请求语义

开发机发送 `prompt`、输入时长、seed、CFG、步数和 `control_profile=prosody`。Worker 只实现官方 `TemplatePipeline` 调用并使用 model 1。旧的 `use_input_audio`、`denoising_strength`、KV-cache 合并和手工分步推理参数已删除。
