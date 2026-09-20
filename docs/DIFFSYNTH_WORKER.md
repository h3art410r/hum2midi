# DiffSynth Worker Agent 运行说明

这份说明给 GPU Worker 部署 Agent 使用。Worker 运行在 Windows + RTX 5060 Ti 16GB，开发机 FastAPI 通过内网地址调用它。Worker 只负责加载 `DiffSynth-Studio/DiffSynth-Music` 和生成音频。

## 运行方式

当前 Worker 只保留官方模型卡的推理路径：`remote/diffsynth_music_server.py` 使用官方的低显存 `ModelConfig`、`TemplatePipeline.from_pretrained(..., lazy_loading=True)` 和官方 `TemplatePipeline(...)` 调用。`remote/worker_mode.txt` 必须是 `official`。

不要加入 denoising anchor、KV cache 合并或量化、模板层 CPU 分页、负分支 cache 复用、手工镜像 pipeline、模型 forward monkey-patch 等逻辑。需要观察性能时只读日志，不改变模型调用。

首次部署时先停止手动启动的 raw Worker，再在仓库根目录启动常驻守护脚本：

```powershell
.\scripts\run_diffsynth_worker_daemon.ps1 -Port 8765 -PollSeconds 30
```

守护脚本每 30 秒拉取 `origin/main`；发现新提交后等待当前任务结束、fast-forward、重启 Worker，并等待 `/health` 恢复。使用 `-PollSeconds 15` 可缩短检查间隔。不要让 raw Worker 和 daemon 同时监听 8765。

守护进程写入 `remote\logs\worker-daemon.log`，每次 Worker 启动的 stdout/stderr 写入同目录的时间戳日志。daemon 自身脚本变更后需要手动重启一次；Worker 代码和 `worker_mode.txt` 的提交会自动部署。

## 启动验收

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health | ConvertTo-Json
```

健康结果应包含：

- `status: ok`
- `execution: official_model_card`
- `control: official_template_pipeline`
- `templates: control, prosody, reference`
- 当前 `build`
- RTX 5060 Ti 的 `device`

如果健康检查不是 `ok`，先查看 daemon 和本次 Worker 的 stdout/stderr，不要静默切换到其他模型或执行模式。

## 生成和日志

`POST /v1/generate` 接受音频、英文 prompt、输入时长、seed、CFG、步数和 `control_profile=prosody`。Worker 只传入官方 Prosody template（`model_id=1`），不传入原始音频重绘参数。输入在 48kHz 下选择 RMS 最大的有效声道并复制为两个相同声道，再截断到 3840 样本的整数倍；生成时长严格取 prosody 张量长度，忽略浏览器的 advisory duration。默认参数为官方示例的 seed=42、CFG=4、steps=50。

生成后可读取：

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/debug/logs?since=0&limit=200" | ConvertTo-Json -Depth 12
```

日志只记录请求、输入解码、条件准备、模型开始/结束、保存、耗时和显存峰值，不记录音频内容、密钥或完整 prompt。
