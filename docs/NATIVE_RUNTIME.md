# Native DiffSynth-Music 运行说明

这是新版本的独立运行入口。它不加载历史 `app/`，也不使用旧的 Stable Audio 或 MIDI 链路。

## 组件边界

```text
手机/桌面浏览器
        |
        v
native.api:8000  -- HTTP 音频上传、任务状态、先完成先展示
        |
        v
native.worker_server:8765  -- GPU、官方 DiffSynth-Music Prosody
```

后端只保存原始/规范化音频、任务清单和生成结果；Worker 只负责模型加载与推理。Worker 由 `scripts/run_diffsynth_worker_daemon.ps1` 常驻管理。守护进程发现主分支有新提交时，会等待当前任务完成、停止旧 Worker、拉取代码、重新安装依赖并做健康检查；守护进程本身不需要因为模型代码改动而手动重启。

## 开发机启动

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r native\requirements.txt
$env:NATIVE_WORKER_URL = "http://192.168.9.100:8765"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_native_backend.ps1 -Port 8000
```

浏览器访问 `http://localhost:8000/`。桌面调试视图使用 `http://localhost:8000/?debug=16x9`。

后端不会在 Worker 不可用时伪造输出；`/api/health` 会明确显示 Worker 的健康状态，任务会保留真实错误。

## GPU Worker 一次性部署

在 RTX 5060 Ti 的 Windows 工作区执行：

```powershell
git pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_diffsynth_worker_daemon.ps1 -Port 8765 -PollSeconds 30
```

不要同时启动旧的 `remote.diffsynth_music_server` 和新 Worker。新守护进程启动的是 `native.worker_server:app`，运行官方 Prosody-only Quick Start：

- `LoadMultiTrackAudio(division_factor=3840)`；
- 直接对哼唱调用 `extract_prosody`，不做 vocal 分离；
- `TemplatePipeline` 的 Prosody `model_id=1`；
- `pipe.default_negative_prompt` 和同一份 Prosody 负向模板；
- 默认 `tiled=True`、CFG 4、50 steps、seed 42、输出时长等于 Prosody 条件时长；
- 输出通过 `soundfile` 保存为 48kHz PCM WAV，避免 TorchCodec 可选依赖导致保存失败。

Worker 的详细阶段日志可读：

```text
http://192.168.9.100:8765/health
http://192.168.9.100:8765/debug/logs?limit=200
```

守护进程日志在 `remote/logs/worker-daemon.log`，每次 Worker 启动的标准输出和错误输出在同一目录的时间戳文件中。

## 后端 API

- `POST /api/generations`：请求体是音频二进制，文件名放在 `X-Audio-Filename`，返回 `202` 和任务 ID。
- `GET /api/generations/{id}`：任务和两个风格的独立状态；Funk 或 Lo-fi 任一完成即可先展示。
- `GET /api/generations/{id}/source`：规范化后的原始输入。
- `GET /api/generations/{id}/audio/funk`、`/audio/lofi`：已完成的 WAV。
- `GET /api/debug/logs`：后端阶段日志。

每个任务的 `runtime/native_jobs/<id>/request.json` 保存输入摘要、模型参数、英文 prompt、中文参考译文和 Worker 诊断信息。中文译文只用于页面和人工评审，发送给模型的始终是英文 prompt。

## 当前边界

第一阶段只启用 Prosody-only，Funk 和 Lo-fi 顺序运行以适应 16GB 显存。Control、Reference、联合条件、FP8 和自定义 KV cache 都不在默认路径中；任何后续优化都必须在固定录音和固定 seed 下与这条官方基线 A/B 对照。
