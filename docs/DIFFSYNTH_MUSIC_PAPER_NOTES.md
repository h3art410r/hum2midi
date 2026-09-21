# DiffSynth-Music 论文复核与当前调用调整

阅读原文：<https://arxiv.org/abs/2609.12774>。官方实现与文档：<https://diffsynth-studio-doc.readthedocs.io/en/latest/Model_Details/DiffSynth-Music.html>。

## 论文对当前问题的解释

DiffSynth-Music 不是一个“把整段音频直接复制成新音频”的 audio-to-audio 模型。它在冻结的 ACE-Step-1.5 DiT 上增加三类 template adapter，并把条件音频编码成逐层 KV memory 注入生成分支。Prosody 条件由音高和包络重合成，刻意减少原始人声的发音/音色信息；因此它适合约束旋律走向和时序，但单独使用时不等于完整的起音与节奏控制。

论文明确说明五种控制可以组合：

- `model_id=0` Control：beats、vocals、accompaniment；vocals 条件包含起音和演唱表现，适合保留哼唱的节奏/句法。
- `model_id=1` Prosody：音高与时间条件，降低原始嗓音和发音的复制。
- `model_id=2` Reference：整体音色/风格参考，不应该承担旋律对齐。

官方 prosody 示例只使用 `model_id=1`，但论文的总体设计目标是组合 KV memory。我们此前只使用 prosody，所以出现“高创意方案反而偶尔保留节奏、其余方案听不出原始节奏”的结果并不意外：prompt 只能影响文本条件，不能补足缺失的起音控制。

## 当前改动

worker 现在同时传入：

```python
template_inputs=[
    {"model_id": 0, "audio": waveform},
    {"model_id": 1, "audio": prosody},
]
```

这样由 Control 保留原始哼唱的起音/节奏，由 Prosody 保留音高和时间，文本 prompt 只负责风格、配器和结构。没有把原始音频作为 `target_audio` 融合回输出，因此仍然不会保留原始人声轨。

## 下一轮最佳实践实验

固定同一个 prompt、seed、时长和 50 steps，只改变控制条件，生成三组：

1. Prosody only：当前正式基线，严格复现官方 Input 5 的条件形态。
2. Control + Prosody：仅用于显式 A/B，验证额外条件是否确实改善旋律身份和节奏保持。
3. Control + Prosody + Reference：只在第 2 组节奏稳定后加入短参考片段，观察音色/制作质量是否提升；Reference 不用于修复节奏。

验收先听“是否还是同一段哼唱的节奏和旋律”，再听创意和制作质量。若组合控制过强导致创意下降，优先降低文本约束、换 prompt 和 seed，不先破坏音频控制条件。
