# Make Anything Musical — Native DiffSynth-Music Demo Spec

- 版本：2.0（全新 DiffSynth-Music 官方 Prosody 双变体链路）
- 日期：2026-09-17
- 目标：把用户真实哼唱直接重制成可试听的 Funk 与 Lo-fi 两个版本，并通过分段日志定位端到端耗时。

## 产品流程

1. 手机浏览器按住按钮录制 5–15 秒真实哼唱；桌面浏览器提供 16:9 调试视图。
2. 用户提交录音，后端把输入统一成 48kHz、双声道 WAV，并保留这份归一化输入供试听；Worker 会选择有效声道、复制成两个相同声道，并按官方 3840 样本块对齐。
3. 新入口是 `native.api`，由 5060Ti Windows CUDA Worker 按 DiffSynth-Music 官方模型卡的 `TemplatePipeline`，直接使用 Prosody 条件生成完整音乐。输入到输出不经过 MIDI，也不叠加原始人声轨或额外伴奏轨。
4. 页面轮询任务状态，Funk 或 Lo-fi 任一完成就先展示对应 WAV；另一个版本继续生成，失败时显示具体错误。
5. 历史 `app/`、`remote/` 和旧试听实验只作对照，不被新入口 import 或隐式调用。

## 当前实现

- 后端：`native/api.py`，Python + FastAPI；任务清单和文件放在 `runtime/native_jobs/`。
- 远端模型：`DiffSynth-Studio/DiffSynth-Music` 官方 Prosody；服务代码在 `native/worker_server.py`，Windows 常驻入口在 `scripts/run_diffsynth_worker_daemon.ps1`，由它启动 `native.worker_server:app`。远端不可用时明确失败，绝不静默回退。
- Worker 采用官方文档的 BF16、低显存 `ModelConfig`、`vram_limit` 和 `TemplatePipeline` 懒加载；只传入官方 Prosody `model_id=1`，不使用自定义 cache 合并、层分页、重绘锚定或 forward 包装。
- 输入格式：M4A/MP4、WebM、WAV、MP3、OGG；服务端统一转为 48kHz、双声道、PCM WAV，不做旧版响度魔改。
- 输出：每个风格生成与官方 Prosody 条件同长的 WAV，保持 48kHz；时长从对齐后的 prosody 张量推导，不接受浏览器时长字段去拉伸或裁剪。
- 前端展示 Funk 和 Lo-fi 的英文原文和中文参考；当前参数固定为官方 Prosody、CFG 4、50 steps、seed 42。中文只供人阅读，模型始终接收英文 prompt。

## API

- `GET /api/prompt-presets`：返回两个风格方案、生成参数、英文 prompt 和中文参考译文。
- `POST /api/generations`：请求体为音频二进制，返回 `202` 和任务 ID；每次顺序生成 Funk 与 Lo-fi。
- `GET /api/generations/{id}`：返回任务状态、模型信息和两个独立方案变体信息。
- `GET /api/generations/{id}/source`：播放归一化后的原始输入。
- `GET /api/generations/{id}/audio/{variant}`：播放已完成的 Funk 或 Lo-fi WAV。
- `GET /api/health`：报告 Native 后端和 CUDA Worker 健康状态。
- `GET /api/debug/logs?since=<cursor>`：返回最近的后端阶段日志，供桌面调试窗口显示。

## 验收标准

- 用真实 5–15 秒录音可创建任务，并最终得到两个或至少一个非空 WAV。
- 结果为模型的完整 audio-to-audio 重制，不包含原始人声的单独叠加轨。
- Funk 和 Lo-fi 结果应让用户听出原始哼唱的旋律与节奏，同时完成完整编曲风格化。
- 后端在模型不可用、超时或单个风格失败时返回明确状态，不伪造成功结果。
- GPU Worker 默认配置使用官方 50 steps；前端轮询不会在正常 15 分钟内超时。任何降步数优化都必须与官方基线 A/B。
- 质量验收以端到端试听为准：原哼唱身份可辨认即可；创意看记忆点、结构和重播意愿。没有用户试听结论时保持待评，声学代理分不能证明创意及格。

## 范围外

- 分轨导出、DAW 编辑、短视频、账号、社区、支付和作品历史。
- 把传统音高分析、MIDI 或旧版 Stable Audio 作为主流程。
- 把本地模型静默替换成其他模型；模型缺失必须让任务失败并显示原因。

## 本地运行前提

开发机运行 `native.api`，通过 `NATIVE_WORKER_URL` 连接 GPU Worker；模型权重、GPU 虚拟环境和运行时任务目录不提交到本仓库。完整启动步骤见 `docs/NATIVE_RUNTIME.md`。
