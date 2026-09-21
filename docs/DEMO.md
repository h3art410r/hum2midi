# Native DiffSynth-Music Demo

这是一个手机优先的真实哼唱到风格化音乐 Demo。新入口不使用 MIDI、YIN 或旧 Stable Audio 作为中间层，直接按 DiffSynth-Music 官方 TemplatePipeline 生成完整音频。主页的“官方样例对照”页还会展示官方 Input 5 的 Prosody-only 输入/输出，并用同一套参数重跑最近一次真实哼唱。

## 启动

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r native\requirements.txt
$env:NATIVE_WORKER_URL = "http://192.168.9.100:8765"
powershell -ExecutionPolicy Bypass -File scripts\start_native_backend.ps1 -Port 8000
```

浏览器访问 `http://localhost:8000/`，官方对照页为 `http://localhost:8000/official`。电脑调试可用 `http://localhost:8000/?debug=16x9`。生产入口和 GPU Worker 的完整拓扑见 [`NATIVE_RUNTIME.md`](NATIVE_RUNTIME.md) 与 [`DEPLOY.md`](DEPLOY.md)。

## 端到端流程

```text
真实 M4A/WAV
  -> 开发机规范化为 48kHz 双声道 PCM WAV
  -> GPU Worker LoadMultiTrackAudio(3840)
  -> extract_prosody（哼唱输入，不做 vocal 分离）
  -> TemplatePipeline Control model_id=0 + Prosody model_id=1
  -> Funk 与 Lo-fi 两个完整 WAV
```

参数基线仍是官方 `tiled=True`、CFG 4、50 steps、seed 42，输出时长取 Prosody 条件长度。当前项目为 5–15 秒哼唱配置了按风格区分的英文正向和负向 prompt：正向保留可辨认的旋律/节奏并完成器乐重编，负向清理原始哼唱、底噪和短片段常见伪影；中文译文仅供人工参考。官方对照页仍可显式使用 `pipe.default_negative_prompt`。

## API

- `POST /api/generations`：音频二进制请求，文件名放在 `X-Audio-Filename`，返回 202 和任务 ID。
- `GET /api/generations/{id}`：返回整体状态以及 Funk/Lo-fi 各自状态；任一完成即可先试听。
- `GET /api/generations/{id}/source`：规范化后的输入音频。
- `GET /api/generations/{id}/audio/funk`、`/audio/lofi`：完成后的输出 WAV。
- `GET /api/health`：后端和 GPU Worker 健康状态。
- `GET /api/debug/logs`：页面轮询的后端阶段日志。
- `GET /official`：官方 Input 5 与最近任务对照页。
- `GET/POST /api/comparison/official`：读取或启动最近一次输入的官方 Prosody-only 重跑。
