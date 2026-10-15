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

不要同时启动旧的 `remote.diffsynth_music_server` 和新 Worker。新守护进程启动的是 `native.worker_server:app`；正式 Funk/Lo-fi 链路运行官方 `TemplatePipeline` Prosody-only 条件，其他 Worker 条件仅供明确隔离的实验请求使用：

- `LoadMultiTrackAudio(division_factor=3840)`；为满足块长只在模型输入末尾补静音，生成后裁掉对齐填充，输出恢复为上传归一化 WAV 的精确采样长度；不分段、不拼接；
- `prosody_source=input|vocals` 是 Worker 内部 A/B 参数：`input` 对原始归一化波形调用 `extract_prosody`；`vocals` 按官方 Quick Start 先用 `pipe.extract_track(audio, track="vocals")`，再调用 `extract_prosody`。正式 API 暂保持 `input` 默认值，待同输入整段 A/B 验证后再决定是否切换；
- 仅显式隔离的 `control=prosody_control` 实验会同时传入 Control `model_id=0` 和 Prosody `model_id=1`；正式 Funk/Lo-fi API 不启用组合条件；
- 正式 Funk/Lo-fi 任务使用 `native/prompts.py` 中按风格配置的负向文本；官方对照任务不传该字段时仍回退到 `pipe.default_negative_prompt`，Prosody 负向模板始终传同一份条件波形；
- 每个风格对整段输入只调用一次官方 `TemplatePipeline`，不按乐句分段生成，也不拼接模型输出。项目规定的 5–15 秒输入最多对应 376 个音频 latent 位置，小于官方 VAE 默认 512 的 tile 大小；因此 `tiled=True` 在此时长内仍是一整块 VAE 解码。输出按输入尾部补零满足 3840 样本块，再裁回归一化输入的精确采样数；默认 CFG 4、50 steps、seed 42；
- 输出通过 `soundfile` 保存为 48kHz PCM WAV，避免 TorchCodec 可选依赖导致保存失败。
- `/health` 必须同时声明 `supports_exact_duration=true` 和 `generation_contract=full_clip_exact_input_samples_v1`；Native API 对每个输出实际读取 WAV 帧数与输入比对。旧 Worker、契约缺失或不等长结果都会显示降级/失败，不会作为已完成音频交付。
- 如果当前 `torchaudio` 安装把读取转发到缺失的 TorchCodec，Worker 会记录该事件并用 `soundfile` 读取已经规范化的 PCM WAV；张量形状、48kHz 和 3840 对齐保持与官方加载器一致。

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

当前正式链路默认只启用官方 Prosody-only 条件，Funk 和 Lo-fi 顺序运行以适应 16GB 显存。Worker 仍保留 `control=control` 与 `control=prosody_control` 作为显式 A/B 模式；联合模式单次 10 秒输入的实测峰值约 15.9 GiB，接近 16GB 上限，不能作为默认链路。没有引入自定义采样、KV cache 拼接或 offload 逻辑；条件 KV memory 由官方 `TemplatePipeline` 完成。
