# Native DiffSynth-Music Demo

这是一个手机优先的真实哼唱到风格化音乐 Demo。新入口不使用 MIDI、YIN 或旧 Stable Audio 作为中间层，直接按 DiffSynth-Music 官方 Prosody TemplatePipeline 生成完整音频。

## 启动

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r native\requirements.txt
$env:NATIVE_WORKER_URL = "http://192.168.9.100:8765"
powershell -ExecutionPolicy Bypass -File scripts\start_native_backend.ps1 -Port 8000
```

浏览器访问 `http://localhost:8000/`。电脑调试可用 `http://localhost:8000/?debug=16x9`。生产入口和 GPU Worker 的完整拓扑见 [`NATIVE_RUNTIME.md`](NATIVE_RUNTIME.md) 与 [`DEPLOY.md`](DEPLOY.md)。

## 端到端流程

```text
真实 M4A/WAV
  -> 开发机规范化为 48kHz 双声道 PCM WAV
  -> GPU Worker LoadMultiTrackAudio(3840)
  -> extract_prosody（哼唱输入，不做 vocal 分离）
  -> TemplatePipeline Prosody model_id=1
  -> Funk 与 Lo-fi 两个完整 WAV
```

参数基线是官方 `tiled=True`、CFG 4、50 steps、seed 42，输出时长取 Prosody 条件长度。模型默认负向文本从 `pipe.default_negative_prompt` 读取；项目只提供 Funk 和 Lo-fi 的短英文正向 prompt，中文译文仅供人工参考。

## API

- `POST /api/generations`：音频二进制请求，文件名放在 `X-Audio-Filename`，返回 202 和任务 ID。
- `GET /api/generations/{id}`：返回整体状态以及 Funk/Lo-fi 各自状态；任一完成即可先试听。
- `GET /api/generations/{id}/source`：规范化后的输入音频。
- `GET /api/generations/{id}/audio/funk`、`/audio/lofi`：完成后的输出 WAV。
- `GET /api/health`：后端和 GPU Worker 健康状态。
- `GET /api/debug/logs`：页面轮询的后端阶段日志。
