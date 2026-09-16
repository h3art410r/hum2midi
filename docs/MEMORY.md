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
