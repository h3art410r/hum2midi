# 短哼唱提示词实验记录

这轮只调整文字条件，不改变 Prosody 提取、`model_id=1`、采样器或 5–15 秒输入时长。输入是用户的真实哼唱，音频条件负责旋律和节奏，文字负责把它重编成完整器乐。

## 设计目标

- 前 1–2 秒尽快进入原始哼唱对应的音乐钩子，避免短输入被长前奏吃掉。
- 保留能让用户认出自己的旋律轮廓、音符时序、乐句节奏和停顿。
- 去掉原始人声、房间底噪和未经处理的录音痕迹。
- 让 Funk/Lo-fi 有明确的低频、鼓组、和声与音色变化，但不指定固定曲名、音符或外部参考。

## 当前默认配置

### Funk

```text
Create a short, hook-first instrumental funk reinterpretation of the supplied hummed melody. Keep its recognizable melodic contour, note timing, phrase rhythm, and pauses as the central hook, but replace the voice with a lively pocket of syncopated drums, deep elastic bass, rhythmic guitar, clavinet, and tight brass accents. Make the short 5–15 second phrase feel like a polished, memorable funk idea with tasteful variation, and no vocals.
```

```text
Raw humming, singing, speech, room noise, microphone hiss, clipping, silence, or the unedited recording. Do not lose the source phrase timing or replace it with an unrelated melody. Avoid a long intro, static drone, random notes, weak drums, muddy bass, harsh distortion, and excessive reverb.
```

### Lo-fi

```text
Create a short, hook-first instrumental lo-fi reinterpretation of the supplied hummed melody. Keep its recognizable melodic contour, note timing, phrase rhythm, and pauses as the central hook, but replace the voice with dusty pocket drums, mellow electric piano, soft bass, gentle chord color, and restrained tape texture. Make the short 5–15 second phrase feel like a warm, memorable lo-fi idea with subtle variation, and no vocals.
```

```text
Raw humming, singing, speech, room noise, microphone hiss, clipping, silence, or the unedited recording. Do not lose the source phrase timing or replace it with an unrelated melody. Avoid a long intro, static drone, random notes, a muddy mix, harsh distortion, and excessive reverb.
```

## 发散候选（暂不进入默认链路）

这些候选用于后续固定同一输入、seed、CFG 和步数时做单变量 A/B，不在没有试听反馈前混入默认配置。

1. **节奏钩子优先**：只保留“短、立即进钩子、原始停顿不变”，把其余编曲交给模型，观察旋律身份是否更稳。
2. **乐队重编优先**：强调低频、鼓组和和声层次，弱化“note timing”等文字约束，观察创意是否提升。
3. **音色替换优先**：强调“完全替换哼唱音色、不能出现原始人声”，但不写具体乐器组合，观察模型自由度。
4. **短片段结构优先**：强调 5–15 秒内有起始、钩子和轻微发展，观察是否减少长前奏和突然截断。

当前默认选择了四者的折中版本。每次只改一个 prompt 或 negative prompt，输出保留在任务清单中，用人工试听判断“听得出原旋律”和“有风格变化”是否同时成立。

