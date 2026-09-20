# 当前决策（2026-09-18）

## 2026-09-20：DiffSynth Worker 细粒度性能埋点

- 同一段 10.943 秒录音在 Control + Prosody、CFG 4、steps 10 下，开发机侧确认上传与 ffmpeg 约 0.38 秒；Worker 请求分别为重绘锚点开启 153.318 秒、关闭 125.613 秒，输出均为约 10.96 秒。瓶颈在 GPU Worker 模型执行，不在公网、反向隧道或本地转码。
- `6a96d12` 为 Worker 追加了只读 `/debug/logs`，并把模板正/负分支、每个 Pipeline Unit、每个 denoise step、VAE decode、保存和显存峰值记录到内存日志；响应头和后端任务状态会携带对应阶段数据。Worker 拉取该提交并重启后才会生效。
- 该埋点通过镜像 `TemplatePipeline.__call__` 的输入合并逻辑保持模型调用语义不变，只增加计时，不改变默认 Control + Prosody、steps 10、denoising strength 0.65。
- Worker 支持显式 `DIFFSYNTH_OFFLOAD_MODE=cpu|dit_cuda|none`：`cpu` 保持 16G 显存下的安全路径；`dit_cuda` 让 DiT 尽量常驻 CUDA 做速度 A/B，可能 OOM；`none` 完全关闭 VRAM 管理，仅用于确认显存上限。默认值仍为 `cpu`，不会自动切换。

## 2026-09-20：单 Funk 快速性能基线

- 当前链路只生成一个 Funk 结果，固定 `Control + Prosody`、CFG 4、steps 10、seed 101、denoising strength 0.65；前端不显示调参面板，参数写入请求和后端日志。
- 真实录音任务 `8608ed2c6a2546d28b012fea99e4808d`（输入 10.943 秒）已完成：上传和 ffmpeg 归一化约 0.55 秒，模型请求耗时 130.15 秒，输出 10.96 秒；性能瓶颈在远端模型请求，不在开发机上传、转码或任务轮询。
- 第一轮提交因客户端把表单字段 `control` 错写为 `control+prosody` 被旧 Worker 立即 HTTP 400 拒绝，已修复为 `control=prosody` + `control_profile=control_prosody`，修复提交为 `db421dd`。
- Worker 需要拉取 `db421dd` 并重启，才能看到新增的 `INPUT_DECODED`、`CONDITIONING_READY`、`MODEL_INFER_START/DONE`、显存峰值和 `AUDIO_SAVE_DONE` 分段日志；开发机后端已重启并运行新代码。
- 速度对照（同一输入、Control + Prosody、CFG 4、seed 101）：steps 10 + denoising 0.65 为 130.15 秒；steps 5 + denoising 0.65 为 87.82 秒；steps 5 且关闭 `input_audio` 重绘锚定为 64.99 秒。由此可见推理有明显固定开销，输入音频 VAE 锚定还会增加约 23 秒；是否关闭锚定要结合听感决定。
- 显式关闭锚定使用 `X-DiffSynth-Denoising-Strength: off`；后端已修复空值诊断字段误把成功结果报成 `float('')` 失败的问题。

用户否定旧“创意分”，体感惊艳仅 0.1；此前通过声学代理门不能证明创意及格。当前转向提示词工程：每轮五个差异明显的纵向方案，前端展示完整内容，用户比较最好/最差并给听感反馈。遵循只需认得出原哼唱，创意评结构和抓耳程度。未反馈候选均待评。 

当前改为五个纵向方案，编号 1–5，分别探索旋律蓝图、律动钩子、和声重作、结构推进、完整再创作。每次上传生成五个结果；本轮重绘强度回退到 0.20、0.30、0.40、0.50、0.60，用于定位 Stable Audio 开始丢失原始节奏的临界点。任务保存英文 prompt、中文参考译文和参数快照。固定种子 101 用于比较，不代表已获听感认可。中文只供我们查看，不发送给模型。

下面是历史实验记录；其中“通过”和“更有创意”仅指当时的代理指标，已被上述判定取代。

# 项目记忆

## 2026-09-17：Stable Audio 本地音频闭环

- 主流程是：真实录音 → ffmpeg 归一化为 44.1kHz 双声道 WAV → Stable Audio 3 TFLite `sm-music` audio-to-audio → 五个纵向方案 WAV。
- 模型权重和推理环境位于仓库同级的 `stable-audio-3-local/optimized/tflite`，不进入 Git；当前开发机使用 LiteRT/XNNPACK CPU。
- Stable Audio provider 位于 `app/stable_audio.py`，通过官方 CLI 子进程调用。主 API 不调用其他音频理解模型，也不使用本地 mock 输出。
- 输出时长读取规范化输入 WAV 的实际时长；输入时长不可读时才使用 10 秒兜底。
- 固定种子 `101`；五个方案的 `init_noise_level` 为 0.35、0.58、0.75、0.88、0.97，分别探索音色、律动、和声、结构和激进再创作。禁止源录音底噪和原始人声泄漏。哪个方向最好、哪个最差由用户试听决定。

## 2026-09-17：真实测试与公网入口

- 用户真实录音完整测试任务 `c2144eee7a04486b96c76c4244d40755`：输入与两个输出均为 10.516 秒，两个音频路由均返回 HTTP 200。
- 页面通过 `/api/debug/logs` 轮询后端阶段日志，覆盖上传、ffmpeg、排队、五个方案开始、完成和失败。
- 公网入口为 `https://kr.sunyongfei.cn/hum2midi/`。远端 Nginx `/hum2midi/` 代理到远端 `127.0.0.1:18000`，开发机用 SSH reverse forwarding 接回本地 8000；模型始终只在开发机运行。
- `scripts/start_hum2midi_tunnel.ps1` 会在隧道断开后每 5 秒重连，用户启动目录已配置自动启动脚本。
- 4 项单元测试通过，Python 编译、前端 JavaScript 语法和公网健康接口均已验证。

## 2026-09-17：历史声学诊断（不再作为验收）

- `docs/audio_eval.py` 和早期候选报告保留作回归排查；其中的频谱“创意分”不能代表用户是否觉得惊艳，也不能标记产品通过。
- `docs/iterate_stable_audio.py` 现在固定真实输入并保存五组纵向方案候选，报告中的听感状态保持 `pending`，等待用户比较五个结果。
- 真实 HTTP 任务只能证明链路成功、输出可播放和时长正确；听感仍由 `docs/listening_eval.py` 记录。

## 当前决策

- 只保留音频到音频的 Stable Audio 端到端路径。
- 结果必须与输入同长，并尽量让用户听出原始哼唱的节奏与旋律身份。
- 2026-09-18 新一轮真实测试任务 `390147af28624b68a6727bf9fa96dd77` 已生成 1–5 五个中间强度方案，参数为 0.76、0.78、0.80、0.83、0.86；提示词明确要求“旋律蓝图/新主旋律”，禁止把哼唱逐音翻奏成弦乐。等待用户听感反馈。
- 用户反馈方案 3/4 的风格方向可以，但原始哼唱节奏已经听不出来，且约束太多。后续 prompt 已压缩为：只要求原始节奏和旋律身份可辨认，其余创作完全开放；仅保留去除原始哼唱与底噪的清理要求。
- 按该反馈生成任务 `de4265fdb91441999bfe660fb53ff99a` 已完成五个结果；五条英文 prompt 均已缩短，保留原始节奏/旋律身份作为核心约束，等待新的端到端试听反馈。
- 远端服务器只负责 HTTPS 入口和代理，不下载模型、不执行推理。
