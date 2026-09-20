# Make Anything Musical — 音频风格化 Demo Spec

- 版本：1.2（DiffSynth-Music 远端 Control + Prosody Funk 最小链路）
- 日期：2026-09-17
- 目标：把用户真实哼唱直接重制成一个可试听的 Funk 版本，并通过分段日志定位端到端耗时。

## 产品流程

1. 手机浏览器按住按钮录制 5–15 秒真实哼唱；桌面浏览器提供 16:9 调试视图。
2. 用户提交录音，后端把输入统一成 44.1kHz、双声道 WAV，并保留这份归一化输入供试听。
3. 音频 provider 由 `H2M_AUDIO_PROVIDER` 显式选择。默认仍是 Stable Audio 3 本地 TFLite `sm-music`；可选择 `diffsynth_remote`，由 5060Ti Windows CUDA worker 使用 DiffSynth-Music Prosody Control 直接从哼唱 prosody 生成完整音乐。两者都不叠加原始人声轨或额外伴奏轨。
4. 页面轮询任务状态，展示一个生成的 Funk WAV；失败时显示具体错误。

## 当前实现

- 后端：Python + FastAPI，任务状态保存在进程内存，文件放在 `data/demo/`。
- 模型：Stability AI 官方 Stable Audio 3 TFLite CLI，默认 `sm-music`，通过子进程隔离模型环境。
- 远端模型：`DiffSynth-Studio/DiffSynth-Music` 官方 Control + Prosody；服务代码在 `remote/diffsynth_music_server.py`，Windows 启动脚本在 `scripts/start_diffsynth_music_server.ps1`。远端不可用时明确失败，绝不静默回退。
- CPU 路径：LiteRT/XNNPACK；`STABLE_AUDIO_THREADS` 控制 CPU 线程数。具备 CUDA 环境时可单独评估官方 medium 路径，但本 Demo 默认使用 CPU 小模型。
- 输入格式：M4A/MP4、WebM、WAV、MP3；服务端统一转 WAV 并做响度归一化。
- 输出：每个任务生成与规范化输入同长的 `1.wav`，保持 44.1kHz 双声道；只有输入时长不可读时才使用 10 秒兜底。
- 前端展示 Funk 的英文原文和中文参考；当前快速迭代参数固定为 Control + Prosody、CFG 4、steps 10、seed 101、denoising strength 0.65。用户通过试听结果和后端分段日志共同评估，不再显示运行参数面板。中文只供人阅读，模型始终接收英文 prompt。

## API

- `GET /api/prompt-presets`：返回 Funk 方案、重绘参数、英文 prompt 和中文参考译文。
- `POST /api/generations`：请求体为音频二进制，返回 `202` 和任务 ID；每次都生成全部五个方案。
- `GET /api/generations/{id}`：返回任务状态、模型信息和五个方案变体信息。
- `GET /api/generations/{id}/source`：播放归一化后的原始输入。
- `GET /api/generations/{id}/audio/{variant}`：播放 Funk 的 WAV。
- `GET /api/health`：报告当前显式选择的 provider；远端模式额外报告 CUDA worker 健康状态。
- `GET /api/debug/logs?since=<cursor>`：返回最近的后端阶段日志，供桌面调试窗口显示。

## 验收标准

- 用真实 5–15 秒录音可创建任务，并最终得到五个非空 WAV。
- 结果为模型的完整 audio-to-audio 重制，不包含原始人声的单独叠加轨。
- Funk 结果应让用户听出原始哼唱的旋律与节奏，同时完成完整编曲风格化。
- 后端在模型不可用、超时或单个风格失败时返回明确状态，不伪造成功结果。
- CPU 默认配置在本地开发机可完成五次与输入同长、8 步生成；前端轮询不会在正常 15 分钟内超时。
- 质量验收以端到端试听为准：原哼唱身份可辨认即可；创意看记忆点、结构和重播意愿。没有用户试听结论时保持待评，声学代理分不能证明创意及格。

## 范围外

- 分轨导出、DAW 编辑、短视频、账号、社区、支付和作品历史。
- 把传统音高分析作为主流程。
- 把本地模型静默替换成其他模型；模型缺失必须让任务失败并显示原因。

## 本地运行前提

项目默认从仓库同级目录读取 `stable-audio-3-local/optimized/tflite`。也可以通过 `STABLE_AUDIO_ROOT` 指向该目录。模型权重和虚拟环境不提交到本仓库。
