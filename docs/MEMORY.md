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
