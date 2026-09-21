"""Constants from the official DiffSynth-Music Input 5 Prosody example."""

from __future__ import annotations

OFFICIAL_PROMPT = "An explosive, high-energy pop-rock track with a strong anime theme song feel."

# This is the lyrics string shipped in the official Quick Start example. It is
# part of the official conditioning recipe even though the input control is
# Prosody-only; keeping it here makes the comparison reproducible.
OFFICIAL_LYRICS = """[Intro]

清新海风里有我们旅途
漆黑海浪上有帆依呀远征
风暴的咆哮不把恐惧藏水手的胸襟
祈祷你像无畏的领航人
懂也不懂的守护航程
你在甲板上留下的刻痕
是我梦的风景

我要送你永不沉的信念
升起代表勇的黑旗幡
我要送你永不沉的誓言
锚连着锚把七海踏遍
你就是烈焰
你就是烈焰
我的血未寒
不灭的烽火燃在你身边
我的血未寒

怒海的狂涛总是起了又平
凝望指着罗盘的星辰
我要把酒全都灌进骨里
陪我一起远行

我要送你永不沉的信念
升起代表勇的黑旗幡
我要送你永不沉的誓言
锚连着锚把七海踏遍
你就是烈焰
你就是烈焰
我的血未寒
不灭的烽火燃在你身边
我的血未寒

祈祷你像无畏的领航人
懂也不懂的守护航程
你在甲板上留下的刻痕
是我梦的风景

我要送你永不沉的信念
升起代表勇的黑旗幡
我要送你永不沉的誓言
锚连着锚把七海踏遍
你就是烈焰
你就是烈焰
我的血未寒
不灭的烽火燃在你身边
我的血未寒

我要送你永不沉的信念
升起代表勇的黑旗幡
我要送你永不沉的誓言
锚连着锚把七海踏遍
你就是烈焰
你就是烈焰
我的血未寒
不灭的烽火燃在你身边
我的血未寒
"""

OFFICIAL_PARAMS = {
    "control": "prosody",
    "model_id": 1,
    "seed": 42,
    "cfg_scale": 4.0,
    "steps": 50,
    "tiled": True,
    "sample_rate": 48000,
    "division_factor": 3840,
    "negative_prompt": "pipe.default_negative_prompt",
}
