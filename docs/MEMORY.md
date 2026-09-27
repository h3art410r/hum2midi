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

## 2026-09-21：Prosody-only 对旋律/节奏保持不足

- 官方论文把 Control 和 Prosody 定义为可组合的独立 KV 条件：Control 负责 beats/vocals/accompaniment 的起音与节奏，Prosody 负责正弦重合成后的音高和时间；联合时拼接各自的 KV memory，而不是拼接输入波形。
- 当前默认 Worker 从同一份规范化哼唱同时生成 `model_id=0` Control 和 `model_id=1` Prosody，仍使用官方 `TemplatePipeline`、模型默认负向 prompt、CFG 4、50 steps、固定 seed 42、时长匹配 Prosody。
- 在真实 `1-哼唱.m4a`（10.516 秒）上用固定 seed、10 steps 做 A/B：Prosody-only 峰值约 8.9 GiB、推理约 34.6 秒；Control-only 峰值约 9.0 GiB、推理约 32.0 秒；联合条件峰值约 15.9 GiB、推理约 76.8 秒，成功完成。联合条件用于先验证旋律/节奏身份，显存不足时可回退 `NATIVE_CONTROL=prosody`。
- 这不是把人声轨混回成品：未传 `target_audio`/`target_track`，只把原始哼唱分别编码为官方 Control/Prosody 条件。后续听感仍需用同一段录音试听确认，不能用频谱代理分数代替人工判断。

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

## 2026-09-21：官方负向 Prompt 与共享 Prosody 缓存

- 已核对 DiffSynth-Music 官方 Quick Start 和本地官方实现：`negative_prompt` 不是由另一个模型临时生成，也不是按 Funk/Lo-fi 名称随意改写；官方调用直接读取 `pipe.default_negative_prompt`。新版本两个风格共用这一官方值，并把实际文本、模型版本写入任务诊断。
- 官方没有 Funk 或 Lo-fi 的内置正向 Prompt；官方 Quick Start 的正向音乐描述只是示例。当前 Funk/Lo-fi 正向词是项目配置，必须与模型默认负向文本分开标注。
- `negative_template_inputs` 与文本 `negative_prompt` 是两条不同的 CFG 分支。Prosody-only 按官方示例把同一份 Prosody 波形传给正、负模板输入；不能用文本负向词替代，也不能把它解释成降噪。
- 固定输入、种子、CFG 和步数的首轮 A/B 只改变正向风格 prompt。只有确认可复现的具体伪影后，才允许把很短的实验性负向后缀作为独立变量，不能直接覆盖官方基线。
- 论文和 pipeline 都支持 KV memory 复用。新后端先做一次官方 `extract_prosody` 和正/负模板 KV cache，再顺序用同一对 cache 生成 Funk、Lo-fi；两种风格共用同一条件，单模型常驻以避免 16GB 显存同时加载两套采样状态。缓存只在单任务生命周期内保留。
- 已确认下一步的改动边界：只按官方 Quick Start 调整 Worker 的模型调用方式，不改模型权重、DiT 内部 forward 或常驻 daemon。Worker 代码推到 `main` 后由 daemon 自动拉取、重启模型子进程并健康检查；daemon 脚本本身不变。

## 2026-09-21：全新 Native Prosody 入口首轮闭环

- 新版本独立放在 `native/`，不从历史 `app/`、旧 MIDI 链路或旧 Stable Audio provider 导入。`native.api` 负责上传、任务清单、规范化和状态；`native.worker_server` 负责官方 DiffSynth-Music Prosody 推理。
- 守护进程启动入口已切到 `scripts/start_native_diffsynth_worker.ps1`。守护进程脚本本身变更需要在 GPU 机器手动重启一次；之后模型代码提交会按原有轮询机制自动拉取、重启 Worker 子进程并健康检查。
- 新前端一次创建 Funk 和 Lo-fi 两个顺序变体；任一变体完成就先展示，两个变体共用官方 Prosody 参数基线，实际发送的英文 prompt 和模型诊断写入任务清单。
- 本地编译、FastAPI 路由、PowerShell 解析检查均通过。用真实 `1-哼唱.m4a`（10.516 秒）对当前远端 Worker 做协议冒烟测试：输入对齐到 10.480 秒，steps=1 时 Funk 39.137 秒、Lo-fi 29.265 秒，输出均为 48kHz 双声道 10.480 秒 WAV，Worker 峰值 reserved 约 8.97 GiB。该结果验证了新后端协议和先出先展示逻辑，不代表官方 50 steps 的最终听感基线。
- 当前主分支提交 `d5a7fe2` 已推送到远端仓库；迁移期间旧 daemon 仍持有旧启动脚本路径，已通过兼容启动入口在下一轮轮询自动切到新 Worker，不需要手动重启 daemon。
- 开发机已启动 `native.api` 监听 8000，并恢复 `scripts/start_hum2midi_tunnel.ps1` 的反向隧道；公网 `/hum2midi/` 和 `/api/health` 已返回 200。公网 502 的直接原因是隧道进程未运行，不是前后端路由故障。迁移后健康接口已确认 `execution=official_prosody_quick_start`，说明旧 daemon 通过兼容入口自动完成了切换。

## 2026-09-21：短哼唱专用 Funk/Lo-fi Prompt 实验

- 针对 5–15 秒真实哼唱，把项目正向 prompt 改成“短片段、先出钩子、保留可辨认旋律轮廓/时序/乐句节奏/停顿，再替换为器乐编曲”，避免长前奏吞掉输入身份。
- Funk 与 Lo-fi 各自使用一段简短的负向文本，清理原始哼唱、歌声、房间底噪、削波、静音和未经处理录音；两者只在风格相关的失败特征上有小差异，不再强制共用模型默认负向词。
- Worker 新增可选 `negative_prompt` 表单字段：正式任务传项目配置，官方对照不传时回退 `pipe.default_negative_prompt`；响应头和日志记录来源。
- 调试页 `/official` 新增“直接生成当前提示词”按钮，复用最近一次已完成的真实哼唱并创建新任务，便于只改 prompt 后直接试听，无需重新录音或上传。
- 手机上传补充了文件名编码：前端用 `encodeURIComponent` 发送 `X-Audio-Filename`，后端解码后再取扩展名，避免中文文件名让浏览器在发请求前抛出无响应错误。
- Native 后端规范化现在明确选择双声道中能量更高的一侧，再复制为两个相同声道；不再让 `ffmpeg -ac 2` 把有效哼唱与弱/噪声声道平均，符合官方 Prosody 输入适配和用户要求。
- 新 Worker 首轮真实冒烟发现当前 DiffSynth 版本的 `LoadMultiTrackAudio` 会通过 `torchaudio` 要求可选 TorchCodec。已加入仅针对该缺失依赖的 soundfile fallback：保持 `[channels, samples]`、48kHz 和 3840 对齐后继续走官方 `extract_prosody` 与 `TemplatePipeline`，不改模型内部。真实输入单步测试成功：Prosody 0.357 秒、推理 33.305 秒、总计 33.674 秒、峰值 reserved 8.969 GiB，输出 10.480 秒 WAV。
- 新 Worker 默认 50 steps 的正式双风格基线任务 `f6d12de8f1d542b29ea0ec64dfb50899` 已完成：真实输入 10.516 秒、Prosody 对齐 10.480 秒；Funk Worker 总计 42.955 秒、推理 41.494 秒、峰值 reserved 8.924 GiB；Lo-fi 总计 39.589 秒、推理 39.222 秒、峰值 reserved 8.969 GiB。两个输出均为 48kHz 双声道 10.480 秒 WAV，前端可按变体独立展示。

## 2026-09-28：主钩子辨识度与 Funk 编曲力度

- 固定真实输入 `debug_recent_input.wav`、seed42/CFG4/50步，先比较 Prosody-only 与显式 Control+Prosody。相同 prompt 将哼唱改写成器乐 Funk，要求旋律与节奏贯穿；CLAP Funk 分别 0.4144/0.3190，Pitch50 分别 0.0078/0.0943。AST 两条人声很低，但声学评估不能替代用户盲听。
- 在 Control+Prosody 下把主钩子指定为清晰 Clavinet、保留原旋律顺序和停顿，伴奏改为切分贝斯、鼓、吉他、灵魂和声及铜管回应。相较一般的“重制 Funk”提示，CLAP 由 0.319 升至 0.351、Pitch50 由 0.094 升至 0.287；覆盖率 0.618。AST 哼唱 0.0008、人声 0.0295。该候选约 78 秒、峰值预留显存 15.879GB，已放进 `/official` 试听实验区。
- 另一条更自由地重写旋律、保留轮廓/标志音程/重音的提示，Pitch50 0.302、CLAP 0.308，没有胜过主钩子方案的综合指标。
- 对清晰主钩子方案只把 CFG4 改为5：CLAP 从0.351升到0.463，但 Pitch50从0.287降到0.158、起音相关从0.667降到0.478、Chroma从0.381降到0.266。CFG5让文本风格更强，却牺牲原旋律的技术保持；目前CFG4更平衡。两条 AST 人声标签都低。
- 同 prompt/seed 下增加 CFG4.5：CLAP0.406、Pitch50 0.221、起音相关0.612、Chroma0.402，介于 CFG4 与 CFG5。风格和旋律音高/节奏之间呈现可量化的 Pareto 权衡；不要只按 CLAP 选参数，需并排盲听。
- 官方 DiffSynth-Music 文档/源码表明省略 `bpm`、`timesignature`、`keyscale` 时会注入 100、4、B minor 文本元数据。为避免默认 B minor 拉偏哼唱，Worker 增加了可选官方 metadata 字段（默认未变），commit `ae2acb1` 已 push 并由 daemon 自动更新到该 build。
- 本音频起音估速约 103 BPM；chroma key profile 最偏 E minor 0.395，其相对大调 G major 0.337，模式判断仍有歧义。仅改变 keyscale=B minor默认→E minor，保持 CP/seed42/CFG4.5/50步/prompt相同：Pitch50 0.221→0.337，median误差170→40 cents，P90 1130→270 cents；但 CLAP Funk 0.406→0.338，起音相关0.612→0.572，Chroma近似不变0.402→0.397。说明调性元数据帮助音高贴合、却可能减弱Funk风格；最终听感未确认，不能自动选用。
- 当前领先方向是“主旋律有清晰乐器承载 + 围绕旋律重写完整 Funk 编曲”；继续一次只变一个变量。单音 Pitch50 和 CLAP 都不能证明听众认出了原哼唱或认为作品惊艳，最终需人工试听打分。

## 2026-09-28：Control+Prosody 风格/旋律后续实验

- 真实输入固定为 `native/static/official/debug_recent_input.wav`，Worker build `ae2acb1`、RTX 5060 Ti、官方 TemplatePipeline、Control+Prosody 显式 A/B、50步。每条生成约 76–82 秒，峰值 reserved 约15.88GB；正式链路没有改动。
- 单变量调性/CFG对照：CFG4.5、seed42 下 E minor 的 Pitch50/CLAP 为0.3372/0.3383，G major为0.1899/0.3957；E minor提高CFG到5后 Pitch50/CLAP为0.1848/0.3666，Chroma为0.4789。调性元数据不设为正式默认。
- 随机种子影响明显。主钩子 Prompt 下 CFG4.5/seed42 的 Pitch50、起音相关、Chroma、Funk CLAP为0.2209/0.6118/0.4023/0.4064；只改 seed7 后为0.6434/0.7221/0.6381/0.3285。seed7/CFG5 为0.6421/0.6616/0.6651/0.3246，升CFG未带来CLAP增益。
- 新开放式再编曲 Prompt 保留原钩子的轮廓、节奏重音、乐句和停顿，允许重新编曲，不再要求音符顺序逐音完全相同；负向词缩短为清理原始哼唱、人声、底噪、无关旋律、长前奏、浑浊低音及削波。同一 Prompt 的seed42诊断为Pitch50/起音/Chroma/CLAP 0.1486/0.4362/0.3669/0.3731，seed7为0.4173/0.6125/0.6274/0.4207。seed7开放式候选相较旧主钩子seed42/CFG4.5基线四项约为0.417/0.613/0.627/0.421，对比基线的0.221/0.612/0.402/0.406，当前是最值得盲听的平衡候选。
- 候选 `experiment_funk_hook_recompose_seed7.wav` 已写入 `/official` 实验清单，评估值也在 sidecar JSON 和 manifest。AST 人声/哼唱低，但这些代理分不能证明惊艳度或人耳身份辨认；需用户试听后才能决定是否作为候选方案。
- 同一开放式 Prompt、E minor、CFG4.5、50步继续只采样 seed17/123：seed17 的 Pitch50/起音/Chroma/CLAP为0.0013/0.5595/0.2113/0.4127，频谱变化更大但音高身份几乎丢失；seed123为0/0.2317/0.3622/0.292。seed7 的0.4173/0.6125/0.6274/0.4207仍是本组唯一的综合平衡样本，证实单纯换seed不是稳定解。
- 固定seed7，只把正向词改为更具体的律动组和段落爆发描述（切分贝斯、ghost-note鼓组、Wah吉他、铜管/风琴重音、乐队收尾），Pitch50/起音/Chroma/CLAP为0/0.2715/0.2302/0.3765；虽然频谱更器乐化，但明显丢失了输入对应。乐器/制作细节写得更强不等于更好的风格化。上述seed17、seed123和强律动Prompt结果已列入官方调试页，当前最佳平衡仍是开放式Prompt seed7，需用户试听判定记忆点与原曲身份。

## 2026-09-28：旋律主钩子保留与 Prosody-only CFG 对照

- 项目负责人要求正式链路和后续对照都不再用 Control + Prosody。`AGENTS.md` 与 `docs/SPEC.md` 已同步：正式生成只走官方 Prosody；除非负责人重新明确授权，不再启用 Control+Prosody。Worker 本进程未重启，当前仍为官方低显存 BF16、TemplatePipeline，build `ae2acb1`。
- 检查发现旧正式提示词相互冲突：正向要求“freely re-compose the notes”，负向排斥“a literal copy of the hummed tune”，并要求“the source singer must not be recognizable”。它会把音符重写、把人声身份去掉，不能清楚表达“旋律保留、编曲改造”。已重写 `native/prompts.py`：同一旋律走向、节奏重音、乐句和停顿作为贯穿主钩子；风格变化放在乐器、律动、和声、回应句及动态；不混入原始录音或人声。
- 用真实录音 `native/static/official/debug_recent_input.wav` 做4条 Prosody-only 输出：seed42、50步、E minor/BPM 等元数据均用模型默认，只对比 CFG4 与 CFG5，Worker 实际推理各约42–44秒。结果已放入 `/official` 实验区，并用新提示词 CFG4 结果刷新页面主试听卡；实验页缩减为4条最新候选，不展示旧 Control+Prosody 条目。
- 诊断值（仅供定位，不代表听感通过）：Funk CFG4 的 Pitch50/起音/Chroma/Funk CLAP 为0.0155/0.1207/0.3522/0.4173；Funk CFG5 为0/0.0992/0.2793/0.3373。Lo-fi CFG4 为0.0052/0.2596/0.3735/0.1628；Lo-fi CFG5 为0.0052/0.3384/0.5410/0.1786。四条 AST 哼唱概率均低于0.0014。CFG5 在 Funk 没有改善，在 Lo-fi 的节奏/Chroma代理指标略好；Pitch50 对复调混音不可靠，CLAP也不是惊艳度量表。
- 页面公网验证：`/hum2midi/official`、新提示词 API、4条实验清单和全部4个 WAV 均 HTTP 200。仅重启本地 FastAPI 以加载新提示词，未重启 Worker。
- 当前客观证据尚不能证明用户听得出原始哼唱或觉得“惊艳”；需并排听 Funk/Lo-fi 的 CFG4 与 CFG5，再据最好/最差具体听感继续迭代。

## 2026-09-28：Prosody-only 随机种子搜索

固定新提示词、官方 Prosody、CFG4、50步和真实输入 `debug_recent_input.wav`，每种风格只改变 seed，测试 seed7 和 seed123；新增 4条生成各约45秒，未使用 Control 或 Control+Prosody。

与 seed42 CFG4 基线相比，Funk seed7 的 Pitch50/起音/Chroma/CLAP 为0.0478/0.2224/0.2953/0.2824，旋律和节奏代理略好但风格相似分更低；seed123为0.0013/0.1992/0.1852/0.3898。Lo-fi seed7 为0.0685/0.3358/0.3129/0.2256，相较seed42的0.0052/0.2596/0.3735/0.1628，音高/起音/CLAP代理上升但Chroma下降；seed123为0.0401/0.2939/0.1749/0.0843。

这组结果没有出现一个客观上同时强风格和强旋律的赢家，说明只靠seed采样不是稳定解；当前调试页精选 seed42 与 seed7 的四条结果让用户盲听比较，seed123留作研究记录。CLAP、Pitch50和复调上的Chroma/起音值不等于听众能认出旋律或觉得惊艳；目标仍待人工试听确认。

## 2026-09-28：录音前置空白的起点对齐实验

检查同一份真实录音 `debug_recent_input.wav` 的 20ms RMS 包络，首个稳定哼唱起音约在1.04秒。原始 Prosody 条件包含约1秒录音前置空白，而正向提示词要求立刻开始，存在“模型先编开头、主旋律后进入”的时间语义冲突。

做了两个官方 Prosody-only A/B（Funk、Lo-fi）：seed42、CFG4、50步、提示词与负向词固定。只把检测到的0.98秒前置空白从开头移到末尾，前面留60ms攻击余量；没有拉伸、调音或改变乐句内部的相对节奏。条件波形仍为10.516秒，官方3840样本切齐后输出10.480秒。Worker 推理约48.2秒/45.8秒，未重启Worker。

粗略代理对比（Pitch50对复调混音不可靠）：Funk原条件的起音/Chroma/CLAP为0.1207/0.3522/0.4173，左移后为0.2930/0.2742/0.3739；起音相关改善、风格相似度略降。Lo-fi原条件为0.2596/0.3735/0.1628，左移后为0.3054/0.5231/0.2142，三项均改善。两条的AST哼唱概率分别为0.000156/0.000918，残留人声检测较低。该A/B让Lo-fi候选成为值得优先试听的下一条，但不能证明用户听得出原旋律或觉得更惊艳。

已把两种风格的原始条件/起点对齐条件四条结果放进 `/official` 实验页，页面对照固定为同输入、同prompt、同seed、同CFG/steps，只改变前置空白的位置。是否采纳到正式预处理，等用户确认听感后再决定。

## 2026-09-28：分句、短 Prompt 与官方 Negative Prompt 消融

- 乐句静音检测得到首句4.08秒、停顿0.56秒、次句有效音频4.32秒。修正实验脚本，避免把首音对齐后移至录音末尾的1.52秒人为静音送入第二句；对两句分别用官方 Prosody-only、seed42/CFG4/50步生成，再拼回10.48秒。
- 分句 Funk 相比整段原始输入基线，起音相关0.1207→0.2560，Chroma 0.3522→0.3488，Funk CLAP 0.4173→0.3836，人声检测0.00149→0.00542；分句 Lo-fi 为起音0.2596→0.3052、Chroma 0.3735→0.1982、CLAP0.1628→0.1784、人声检测0.00264→0.06457。分句没有整体胜出，Lo-fi 明显退化，不进入正式链路。
- 把正向 prompt 压缩成更开放的短描述（固定首音对齐条件、负向词、seed/CFG/steps）后，Funk/Lo-fi 起音代理升至0.3438/0.3908，但风格 CLAP 降至0.1981/0.1295，Chroma降至0.2420/0.3299；说明仅删掉编曲细节并不能取得“更有风格且保旋律”。不采纳为正式 prompt。
- 固定当前正向词与其余参数，把自定义清理型 negative prompt 换成 `pipe.default_negative_prompt`：Funk 的起音/Chroma/CLAP从0.2930/0.2742/0.3739变为0.1952/0.4293/0.3460；Lo-fi从0.3054/0.5231/0.2142变为0.1881/0.3470/0.1389。官方默认值没有总体优势；暂时保留项目自定义 negative prompt。上述均为代理指标，不代表听觉通过。
- 新读 DiffSynth-Music 官方 Quick Start：`model_id=1` Prosody 用同一旋律波形同时传正、负模板条件；`model_id=2` Reference 是官方音色参考条件，Quick Start 示例只传正向 `template_inputs`，负向文本为空。论文说明各模板 KV memory 可组合，但没有提供 Prosody+Reference 的固定组合示例。
- 为直接检验“Prosody保旋律 + Reference增音色/风格”已在 Worker 增加一个显式 `prosody_reference` 实验模式及第二音频上传参数；默认/正式 API 仍只走 Prosody，不使用 Control。调试脚本用本项目先前生成的同风格输出作 Reference，先跑1步显存与协议冒烟，再决定是否运行50步 A/B；当前变更尚未提交/推送，实验结果待定。Reference 模式需要额外模板显存，若单步峰值逼近16GB即停止。
