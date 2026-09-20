# 当前决策（2026-09-18）

## 2026-09-21：启动全新原生模型后端版本

- 用户决定不再沿用旧版后端代码和历史实现路径，单独建立一个全新版本。
- 新版本以 `docs/SOTA_NATIVE_REBUILD_SPEC.md` 为边界：旧版 `app/`、`remote/`、脚本、MIDI/YIN、Stable Audio、Qwen、历史 prompt、显存魔改和缓存逻辑都不能作为新版本依赖。
- 新版本先从空服务骨架实现真实音频到官方模型到音频输出的最小链路，再建立质量基线；旧版只用于历史对照，旧缓存不得冒充新结果。
- 新版本当前确定的模型方案是 `DiffSynth-Music` 官方 Prosody + `TemplatePipeline`，官方基线参数为 `tiled=True`、CFG 4、50 steps、seed 42；模型只在独立 RTX 5060 Ti Worker 中运行，FastAPI 不加载模型。
- 已复核 DiffSynth-Music 原论文：其核心是冻结 ACE-Step-1.5-XL-SFT DiT + Control/Prosody/Reference 模板，把音频条件变成逐层 KV memory 注入生成分支；Prosody 用 pYIN 音高轨迹和包络正弦重合成，保留音高与时间但削弱音色和发音。论文的 50 步 CFG4 实验来自带歌词歌曲，不能直接当作手机哼唱的质量保证。
- 新版本先保持 Prosody-only 的干净基线；若旋律身份不足，下一项论文一致的实验是 Control + Prosody。Reference 只用于风格/音色，不用于修复节奏。
- 当前明确不启用组合条件：Control、Reference 和联合条件在 16GB GPU 上暂不进入默认链路，必须先独立验证显存安全，避免把组合方案混入 Prosody 基线。
- Quick Start 复核确认：正弦波 Prosody 重合成发生在工程侧 `extract_prosody` 预处理，不是模型内部动态完成；Worker 先做 pYIN + 包络重合成，再把条件波形交给 `TemplatePipeline(model_id=1)`。
- 新版本首轮输出改为两个独立变体：Funk 和 Lo-fi，共用同一份 Prosody 条件，只改变风格 prompt。后端按变体更新状态，前端收到任意一个完成结果就立即展示，不等待两个结果都返回。

## 2026-09-21：固定输入试听实验室

- 为了比较“效果好但慢”的旧路径和当前路径，新增 `/listen` 试听页及主页入口。页面复用最近一次真实录音，不要求用户再次哼唱或上传。
- `GET /api/listen/sample` 报告固定输入和已有缓存；`POST /api/listen/generate` 复用同一个 DiffSynth 生成链路，结果仍进入标准任务状态和日志。
- 已用固定输入跑通一份 Funk：输入 9.428 秒，输出 9.36 秒，Worker 推理 38.602 秒、总计 38.935 秒，峰值显存 8.64 GiB。试听页会优先展示缓存结果。

## 2026-09-21：按官方 Prosody 示例统一输入与参数

- 用户要求按官方 DiffSynth-Music Prosody 示例重新跑一版，不再让浏览器 advisory duration 拉伸或裁剪模型输入。
- Worker 保持官方 `TemplatePipeline` 调用：Prosody `model_id=1`、`tiled=True`、CFG 4、steps 50、seed 42、时长取 prosody 张量长度。
- 开发机归一化输入改为 48kHz；Worker 在解码后按每个声道 RMS 选择有效哼唱声道，复制为两个完全相同的声道，再按官方 `division_factor=3840` 截断对齐。这样不会把一条有效声道和一条静音声道平均，避免输入能量被削弱。
- Worker 日志新增声道 RMS、选择结果、复制声道数、3840 对齐样本数和 prosody 条件耗时；响应头的 conditioning 时间改为真实条件提取耗时。
- Funk prompt 收敛为简短的官方风格描述，旋律/节奏保持主要交给 Prosody 条件，不再用长串文字约束模型发挥。

## 2026-09-21：恢复官方 DiffSynth-Music 推理路径

- 用户反馈自定义显存、缓存和重绘逻辑同时影响听感与性能，决定整体回退，不再继续局部打补丁。
- Worker 已整体重写为官方模型卡的低显存 `ModelConfig`、`vram_limit`、`TemplatePipeline(..., lazy_loading=True)` 和官方 Prosody `model_id=1` 直接模板调用；删除了 denoising anchor、KV cache 合并/量化、模板层分页、负分支复用和 pipeline 内部 monkey-patch。
- `remote/worker_mode.txt` 改为 `official`。前后端不再发送或展示 `use_input_audio`、`denoising_strength`；步数恢复官方示例默认 50。旧实验数据保留为历史记录，不用于判断新路径。

## 2026-09-21：0.65 听感过保守，默认改为 0.85

- 用户试听反馈当前默认结果与原始哼唱听感过于接近；后端日志确认不是前端拿错音频，任务 `f480ecbc357c48829b0431e156616f79` 实际使用 `Control + Prosody`、CFG4、steps10、seed101、denoising strength 0.65，并成功生成 9.2 秒输出。
- 在同一录音、同一 prompt、同一 seed 下直接做了两个 Worker A/B：denoise 0.85 的任务 `a75bd72f67` 用时 34.9 秒、峰值 8.78GiB；关闭 `input_audio` 锚定的任务 `261dcb408a` 用时 33.8 秒、峰值 8.78GiB。两者都没有显存超额。
- 先只改变一个变量，将前端和后端默认 denoising strength 从 0.65 调到 0.85，提交 `bddae21` 已部署到公网；Control+Prosody、steps10、CFG4、seed101 保持不变，便于下一轮听感归因。

## 2026-09-21：16GB 纯显卡/常驻 DiT A/B（修复后）

- 目标配置保持不变：同一段 10.943 秒真实录音、Control + Prosody、CFG4、steps10、seed101、denoising 0.65。实验提交依次为 `881da7a`（`dit_cuda`）、`5c73c07`（`none`）和 `c8487d3`（恢复 `cpu` 默认）。
- 修复后的 `dit_cuda`（DiT 常驻 CUDA，模板和条件模块按阶段换入）成功完成：Worker 推理 38.825 秒、端到端 41.088 秒，峰值 allocated 8.74GiB、reserved 8.90GiB，物理显存约 15.90GiB，没有超额分配。输出与既有同参数 CPU 输出 `1.wav` SHA-256 完全一致，说明这次只改变设备驻留方式，没有改变生成效果。
- 完全 `none`（主模型、条件模块和 VAE 全部常驻 CUDA）也返回了音频，但 `CUDA_BEFORE_TEMPLATE` 已占 10.54GiB，模板阶段峰值达到 19.12GiB allocated / 19.29GiB reserved；这是 WDDM 超额迁移，不是 16GB 卡内安全运行。它反而用了 57.886 秒，不能作为纯显卡速度结论，也不能作为生产模式。
- 当前 Worker 已恢复 `remote/worker_mode.txt=cpu`，健康检查为构建 `c8487d3`；因此线上仍走 16GB 安全路径。若只做同一配置的速度实验，可临时使用 `dit_cuda`，不要把 `none` 设为常驻。


## 2026-09-21：16GB Worker 显存闭环

- `dit_cuda` 在 Control + Prosody、10.943 秒输入上会在负向模板阶段 OOM；即使设置 15.15GiB 动态预算，实测峰值仍约 27GB。
- 根因不是模型推理必然需要 17GB，而是自定义调用 `TemplatePipeline.call_single_side` 时没有包 `torch.no_grad()`，模板前向图把权重和中间激活留在 CUDA；同时两个模板 cache 也不应同时以 BF16 常驻。
- Worker 构建 `e954ec9` 已切回 `remote/worker_mode.txt=cpu`，并启用：模板按 block CPU offload、Control/Prosody cache 在 CPU 合并、正负相同 cache 复用、模板推理 `no_grad`。8-bit KV 压缩保留为可选环境变量，默认关闭以保持精度。
- 同一真实录音、Control + Prosody、CFG4、denoising 0.65、seed101 的 steps5 任务 `a65be9d9fc` 成功完成：总耗时 60.83 秒、输出 10.96 秒、峰值 allocated 7.85GiB、reserved 7.88GiB；steps10 任务 `dbf3648c5f` 也成功，峰值同样 7.84GiB、总耗时 62.33 秒。
- 结论：16GB 显存完全够用，当前安全路径不依赖 WDDM 超额分配；如果未来输入时长显著增加，先观察 `/debug/logs` 的阶段显存，再考虑显式开启 `DIFFSYNTH_QUANTIZE_TEMPLATE_KV=1`。

## 2026-09-20：DiT 常驻 CUDA 性能 A/B

- Worker 已按 `remote/worker_mode.txt` 使用 `DIFFSYNTH_OFFLOAD_MODE=dit_cuda` 启动，健康检查返回 RTX 5060 Ti、构建 `3d09604`，模型加载约 38.2 秒。
- 同一段 10.943 秒录音、Control + Prosody、CFG 4、steps 10、seed 101、重绘强度 0.65 的真实任务 `f429f9bcc9fa42aea10af1c3adedfe2d` 成功完成。Worker 总耗时 196.759 秒，开发机观测到请求耗时 196.94 秒，输出 10.96 秒。
- 细分耗时：正向模板 33.377 秒、负向模板 16.925 秒；输入音频编码 6.430 秒；DiT 首次切换 15.798 秒；10 个 denoise step 分别约 9.47–10.56 秒；VAE 解码 6.985 秒；保存 0.041 秒。
- 该配置没有比之前 CPU offload 的约 197 秒实测明显变快。PyTorch 记录峰值 allocated 26.975GB、reserved 27.031GB，而显卡物理显存约 15.90GB，说明当前运行仍在承受显存超额/动态换入压力，不能视为完整驻留显存的安全模式；暂不尝试 `none`，避免无意义的 OOM。
- 下一轮性能实验优先保持 `dit_cuda`，降低 steps 或做 CFG 4 与 CFG 1 的 A/B；当前每一步约 10 秒，固定模板与设备准备约占一半以上总耗时。
- 同时修正 Worker 响应头 `X-DiffSynth-Conditioning-Seconds`：此前误填了整个请求耗时，现在只记录 `CONDITIONING_READY` 阶段，避免后端诊断误导。
- 修正后的响应头已在 Worker 构建 `4448455` 生效。相同输入改用 steps 5 的任务 `a0ba21ab94` 成功完成：Worker 141.699 秒、开发机请求 141.984 秒，conditioning 2.191 秒，模型推理 139.360 秒，输出仍为 10.96 秒。
- steps 5 的 5 个 denoise step 分别约 9.325、8.836、9.060、8.843、9.096 秒；相对 steps 10 的约 196.97 秒节省约 55 秒（约 28%），但模板正/负分支仍约 53 秒，固定开销明显。峰值 allocated/reserved 仍约 26.98/27.03GB，显存压力没有因 steps 降低而消失。
- 同样 steps 5 改用 CFG 1 的任务 `2e05d1cdee` 成功完成：Worker 95.619 秒、开发机请求 96.892 秒，模型推理 94.986 秒，5 个 denoise step 各约 3.64–3.77 秒。相对 CFG 4 的 steps 5 再节省约 46 秒；这说明 CFG 双分支是当前最直接的速度杠杆，但 CFG 1 需要试听确认是否牺牲风格遵循度。
- `CFG=1、steps=10` 的任务 `f933c22d4b` 成功完成：Worker 155.708 秒、开发机请求 156.057 秒，模型推理 153.359 秒，10 个 denoise step 各约 5.31–5.71 秒。相比 CFG 4、steps 10 的约 196.97 秒节省约 41 秒；相比 CFG 1、steps 5 的约 96.89 秒，增加步数主要换取更长推理时间，是否值得由试听决定。

## 2026-09-20：Worker 常驻自动更新

- 新增 `scripts/run_diffsynth_worker_daemon.ps1` 和 `.cmd` 入口。首次启动后 daemon 负责拥有 Worker 子进程、保持端口存活、每 15 或 30 秒 fetch `origin/main`，等待正在生成的请求结束后 fast-forward 并自动重新部署模型。
- daemon 将生命周期和每次 Worker 启动的 stdout/stderr 写入 `remote/logs/`，工作区有未提交改动或 Git 分叉时拒绝自动更新；新版本健康检查失败会回滚到更新前的 clean commit。daemon 自身脚本变更仍需手动重启一次。

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

