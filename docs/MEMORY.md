# 项目实验记忆

## 2026-09-16：SoundFont 中间版本

- Git 初始提交：`4b00575`，tag：`v0.1.0-soundfont`。
- FluidSynth 2.6.0 + GeneralUser GS 已在 Windows 本机实渲染成功。
- 应用通过 `app/audio_renderer.py` 自动选择 SoundFont；缺少外部音源时回退程序化合成器。
- 二进制和音源在被忽略的 `data/` 目录，不进入 Git。

## 2026-09-16：发散研究结论

- 只扩充 MIDI 音符会很快变成“正确但像循环”的 Demo。
- 真实感主要来自采样层、演奏法、力度/微时差、段落动态和总线处理，而不是继续收紧 MIDI Prompt。
- SF2 适合通用基线；SFZ 更适合力度分层、round-robin、keyswitch、legato 和真实鼓组行为。后续可评估开源 `sfizz` 与 CC0 的 VSCO/VCSL 素材。
- 更大胆的路线是让云端模型输出段落、角色、演奏法和能量曲线组成的 `ArrangementGraph`，再由程序生成 MIDI 与渲染控制，而不是让模型只返回音符数组。

## 2026-09-16：演奏层实验

- `arrangement_to_midi()` 新增 GM `CC7/CC10/CC91/CC93`：音量、声像、混响、合唱。
- 非主奏伴奏使用 `H2M_PERFORMANCE_SEED` 产生可复现的微小时差和力度变化；主奏的核心起音、时值和音高保持不变。
- `app/audio_renderer.py` 新增段落能量弧线：开头渐入、第二段轻推、结尾收束；FluidSynth 输出最后统一归一化到峰值 0.82。
- 单元测试从 18 项增加到 19 项，全部通过。

## 2026-09-16：真实端到端复测

- 输入：项目已有真实哼唱 M4A（小星星，两句 14 音）。
- job：`47401e1804974ddfae21b59d0b64c0b9`。
- canonical MIDI：14 音，102.56 BPM，句界 5.64 秒。
- Funk 风格 MIDI：106 音；Lofi 风格 MIDI：48 音。
- 两个音频路由 HTTP 200，API `renderers` 均为 `fluidsynth-soundfont`。
- 音符数量、BPM 和 MIDI 往返校验没有因演奏层变化而退化。
- 当前尚未把“惊艳度”视为机器已证明的指标，必须继续用手机盲听 A/B。

## 下一轮执行顺序

1. 为 Funk/Lofi 建立轻量专用采样 profile 和演奏法映射。
2. 让风格模型输出段落/能量元数据，程序生成可解释的 ArrangementGraph。
3. 用固定 seed 做多个可复现的人性化版本，保留自动旋律约束并做盲听选择。
4. 评估 SFZ 专用库和可选的云端最终总线处理；任何新服务都要记录成本、延迟、许可证和旋律保持率。

## 2026-09-16：demo 生产机部署

- 目标机：`ubuntu@kr.sunyongfei.cn`，Ubuntu 24.04 / Python 3.12。
- 部署目录：`/home/ubuntu/hum2midi`；systemd 服务 `hum2midi.service`，Uvicorn 监听 8000。
- 已安装 FFmpeg、FluidSynth、`fluid-soundfont-gm`；服务器健康检查报告 `fluidsynth-soundfont`。
- Nginx 已做 80→8000 反代，并由 Certbot 配置 HTTPS，HTTP 自动 301 到 `https://kr.sunyongfei.cn`。
- API 密钥通过服务器 `.env` 注入，权限 600，不进入 Git、页面或日志。
- 远程真实录音 job `fe96c26e463d472c857ddf76c69a8398` 完成：canonical 14 音，Funk/Lofi 风格 MIDI 137/46 音符，两个音频接口返回 200。
- 部署细节见 `docs/DEPLOY.md`。

## 2026-09-16：线上试听响度修复

- 线上 SoundFont WAV 的原始峰值约 `-1.7 dBFS`，但平均电平约 `-18.7/-16.8 dBFS`，确认问题是动态范围偏大，不是前端播放器音量被设低。
- SoundFont 母带新增轻度总线压缩（Funk ratio 2.2、Lofi ratio 2.5）和补偿增益，峰值目标从 `-1.7 dBFS` 提到 `-0.7 dBFS`，同时保留段落能量弧线与停顿。
- 生产机已重启并重渲染 `fe96c26e463d472c857ddf76c69a8398`；新测得 Funk 平均电平 `-16.7 dB`、Lofi `-15.8 dB`，两个接口继续返回 200。
- 音频接口增加 `Cache-Control: no-store`，避免浏览器继续播放响度修复前缓存的同名 WAV。

## 2026-09-16：低音量原始录音修复

- 复查用户原始 M4A 发现源文件峰值约 `-11.6 dBFS`，此前的峰值母带修复无法解决“原始录音试听很小”，低电平还会触发“没有检测到足够清晰的连续哼唱音符”。
- `app/main.py` 的分析副本转换增加 FFmpeg `loudnorm=I=-14:TP=-1.0:LRA=7`；只归一化服务端分析副本，不改写用户上传的原始文件。用同一真实录音线上复测得到 14 音、两句、102.56 BPM，说明归一化没有改变旋律结果。
- `app/static/index.html` 的原始试听通过 Web Audio 2.4x makeup gain 与软压缩播放，保留原始上传字节；不依赖把 HTML audio 的 `volume` 设到超过 1。
- `app/audio_renderer.py` 增加 RMS 复测、软限幅和两轮有效段增益，避免 Lofi 因高峰均比被单纯峰值归一化压回低音量。线上重渲染 job `09d79eef1be2448b8645ce58d3f13bd4`：Funk 平均 `-12.4 dB`、峰值 `-1.7 dBFS`；Lofi 平均 `-13.7 dB`、峰值 `-0.7 dBFS`。
- 本轮 `.venv\Scripts\python.exe -m unittest discover -s tests -q`：19 项通过。

## 2026-09-16：平板页面可用性修复

- iPad/触摸设备此前被 `min-width:760px` 误套电脑端 16:9 固定高度，页面 `overflow:hidden` 导致下方“生成我的版本”按钮被裁掉。现在 16:9 只对 `min-width:1024px` 且支持 hover 的桌面调试视图启用，触摸设备使用自然高度和滚动页面。
- 录音圆形按钮增加 iOS 长按保护：禁止选字、拖拽、系统 callout 和 gesturestart，避免长按复制页面文字。
- 已部署并通过线上页面内容校验：`https://kr.sunyongfei.cn/`，健康检查正常。

## 2026-09-17：音准细节保留实验

- 真实录音当前 YIN、独立谐波频谱和 Basic Pitch 基准的整数音高基本一致；剩余听感偏差主要来自人声滑音及整数半音量化丢失的音分。Qwen Omni 直接逐音输出仍会生成固定重复音，不能作为精确替代。
- `pitch_tracking.py` 为每个事件保存 `pitch_cents`（稳定基频相对整数 MIDI 的音分偏移）；`ir.py` 在 note-on 前写入标准 MIDI pitchwheel（默认 GM ±2 半音范围），整数音符、起点及时值保持不变，回放可跟随真实演唱的细微音高。
- 19 项既有单元测试通过；真实录音生成的 MIDI 往返仍为 14 个事件，新增 pitchwheel 不改变 `midi_summary` 的音符时间线。
- pYIN/Praat 离线交叉实验未比当前 YIN 减少稳定音符错误，且首次 pYIN 编译耗时很高，因此不纳入线上路径。

- 线上 `melody` 调试数据现额外返回 `pitch_cents`，页面音符详情显示音分偏移，便于现场确认“整数音名正确但播放仍偏”的情况。

- canonical MIDI 现在显式设置 GM Pitch Bend Range 为 ±2 半音（RPN 101/100/6/38），避免不同播放器对 `pitch_cents` 的解释不一致。

## 2026-09-17：尾部同音碎片修复

- 生产机最近三份真实录音均在结尾出现同音短片段（约 0.21–0.26 秒）被误切成额外音符，导致 15 音。新增保守合并：相邻同 MIDI 音、间隔接近零且一段小于 0.30 秒时合并。三份输入均稳定为 14 音；20 项测试通过，快速重复音符测试未被合并。
- 修复已部署至 `kr.sunyongfei.cn`，生产机直接重跑三份现有 `audio_16k.wav` 均得到 14 音，健康检查正常。

## 2026-09-17：转谱 review 重构与多模态 A/B

- 不再在 `main.py` 里分支堆叠 provider；`app/transcription.py` 现在定义 `TranscriptionEngine` 协议，并提供 `DspTranscriptionEngine` 与可选 `KlangioTranscriptionEngine`。`TRANSCRIPTION_ENGINE=auto` 仅在配置 `KLANGIO_API_KEY` 时选择云端，云端错误不会静默 fallback。
- Qwen Omni 的两种窄任务实验（完整 MIDI JSON、±1 半音候选选择）都未达到逐音可靠性；实验脚本保留在 `docs/probe_multimodal_note_ranking.py`，结果写入 `docs/MODEL_REVIEW.md`，没有污染生产路径。
- YIN、Basic Pitch、pYIN、Praat 的交叉结果支持当前整数音高轮廓；剩余误差主要是人声滑音/音分和“实际哼唱”与“标准曲谱”的目标差异。
- 新增 Klangio Vocal→MIDI provider adapter，采用官方异步转录任务和 MIDI 下载接口。没有配置密钥时不宣称效果通过；下一次应在同一原始 WAV 上做 DSP vs Klangio 的盲测，记录准确率、延迟、费用和失败率。
- review 重构已提交为 `5588099` 并同步到 `kr.sunyongfei.cn`。线上健康检查正常；真实原始 M4A 新 job `1c1ef1b50322406abe3e6087f2516d90` 完成，14 音、102.56 BPM、Funk/Lofi 音频均由 FluidSynth SoundFont 返回 200。该 job 同时记录了 `cloud_pitch_contour` 和实际 `transcription_engine=dsp-yin`，便于后续对照。
- 随机候选 A/B 复测：同一真实 WAV 的 3 个音符、每个 2 次、候选顺序随机，Qwen Omni 命中率 `0/6`；回答标签随音符固定为 A/C，映射到的实际音高随顺序变化。该证据支持停止把通用 Omni 当逐音音高判别器。
- 整段候选复测：候选复用原始人声片段和节奏，仅改整段音高序列，3 次随机顺序选择为 `smoothed`、`motif`、`smoothed`，没有选择 DSP 实测序列；因此也不能把通用 Omni 当作可靠的旋律校正器。
- 调研并接入腾讯多媒体实验室 `vocalMidi` provider：官方能力是人声转录、计算音高和区间并输出 MIDI/JSON。由于腾讯任务只接受可访问 URL，应用新增随机 job id 的临时规范化 WAV 路由；启用前需要 `TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY`、`H2M_PUBLIC_BASE_URL`，没有凭据不宣称效果。
- 线上新增 `melody.wav` canonical MIDI 试听路由和页面卡片；job `64595c59ed27486687168b6c65a54904` 实测 `renderers.melody=fluidsynth-soundfont`，可直接与原始录音及两种风格音频盲听对照。
- Basic Pitch 参数扫描结果：默认参数 23 个碎音；提高 onset/frame 阈值虽能得到 14–15 个音，但会吞重复音或错配起音，不能稳定优于 DSP 基线，未进入生产。
- 调性候选 A/B：以结尾音推断大调、将半音边界音映射到级内音，保留原人声音质感和节奏；Qwen Omni 4 次随机复测选 `dsp` 2 次、`smoothed` 1 次、`diatonic` 1 次，结果不稳定，未自动应用。
- Omni 相邻半音间隔实验：Plus/Flash 都能数到 14 音两句，但同一 Prompt 给出不同且错误的间隔序列，故不进入逐音校正链路。
