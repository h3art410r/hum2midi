# MIDI 音源与渲染方案

## 结论

Demo 的 MIDI 已经包含真实的乐器语义（program、力度、鼓组、和声声部）。之前的程序化合成器只用波形模拟这些语义，所以容易听起来像电子音。现在增加了可选的 FluidSynth SoundFont 渲染后端：FluidSynth 读取标准 MIDI 和 SF2/SF3 音源，按乐器采样、力度、包络、鼓组和效果器渲染 WAV。

应用默认 `AUDIO_RENDERER=auto`：检测到 FluidSynth 和音源时使用 SoundFont；未安装时保留原有程序化合成器，方便干净环境启动。设置 `AUDIO_RENDERER=soundfont` 可将缺少音源视为明确错误，便于验收时避免误用回退音色。

## 音源候选

| 音源 | 适合用途 | 备注 |
| --- | --- | --- |
| GeneralUser GS | Demo 默认音源 | 约 30.7 MB 内存、261 个预置、13 套鼓组；许可允许个人、商业音乐制作和软件项目使用，但上游也提示部分样本来源需要自行审计。 |
| FluidR3 GM/GS | 更高质量的通用基线 | FluidSynth 官方文档列为可用的 Creative Commons 音源；体积和音色覆盖适合多乐器 Demo。 |
| MuseScore General | 后续高质量 profile | 官方页面列出 128 个以上乐器和鼓组，MIT；文件约 208 MB，适合用户自选下载，不作为 Demo 默认依赖。 |
| TimGM6mb | 低资源 fallback | 约 6 MB，启动快但真实感较弱。 |

本项目默认使用 GeneralUser GS，是体积、覆盖面和授权可读性之间的折中。音源文件没有提交到 Git，放在被忽略的 `data/soundfonts/` 目录；发布时应随安装包单独附带许可证和来源，或让用户自行下载。

## 本机安装/验证

Windows 可以从 FluidSynth 官方 Releases 下载 `fluidsynth-v*-win10-x64-cpp11.zip`，解压到 `data/fluidsynth/`。将一个获许可的 `.sf2` 或 `.sf3` 放入 `data/soundfonts/`，或者在 `.env` 指定：

```text
AUDIO_RENDERER=auto
FLUIDSYNTH_BIN=C:\\path\\to\\fluidsynth.exe
SOUNDFONT_PATH=C:\\path\\to\\GeneralUser-GS.sf2
```

直接验证：

```powershell
.venv\Scripts\python.exe -c "from app.audio_renderer import renderer_status; print(renderer_status())"
```

应输出 `fluidsynth-soundfont`。随后从页面提交真实录音，生成任务的 API 响应会在 `renderers` 字段标出每个风格实际使用的后端。当前 Funk/Lofi 的 MIDI program 已分别映射到电吉他/Clav、电钢琴、贝斯、Pad 和 GM 鼓组；FluidSynth 负责把这些 MIDI 语义换成采样音色，Lofi/Funk 的后处理只做整体滤波、空间和饱和度，不增加原始旋律轨道。

## 来源

- FluidSynth 官方文档：SoundFont 2/3 软件合成器及安装说明。
- GeneralUser GS 上游仓库及许可证。
- MuseScore 官方 SoundFont 页面。
