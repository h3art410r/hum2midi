"""Project-owned prompts for the clean native generation path.

These prompts target short (roughly 5--15 second) hummed inputs. The official
audio condition supplies melodic and rhythmic information; text asks the model
to turn it into a compact instrumental arrangement without losing that identity.
"""

from __future__ import annotations


STYLE_PROMPTS: dict[str, dict[str, str]] = {
    "funk": {
        "name": "Funk",
        "prompt": (
            "Start immediately at 0:00 with the supplied hummed melody; use the full short phrase and do not spend time on an intro, prelude, pickup, or ambient lead-in. "
            "Create a short, hook-first instrumental funk reinterpretation of that melody. "
            "Make the supplied humming the continuous, foreground lead hook from the first note to the last: closely "
            "follow its pitch contour, note order, note entrances, beat placement, pulse, phrase rhythm, accents, "
            "pauses, and full phrase duration. Build the groove, harmony, fills, and variations around that recognizably "
            "transformed hook; enhance it, but never abandon it after the opening or substitute a different lead melody. "
            "Replace the voice with a lively pocket of syncopated drums, deep elastic bass, rhythmic guitar, clavinet, "
            "and tight brass accents. Make the entire 5–15 second phrase feel like a polished, memorable funk idea with no vocals."
        ),
        "translation": "从 0:00 立即进入输入的哼唱旋律，不要花时间做前奏、铺垫、弱起或环境引入；用完整短乐句做器乐 Funk 改编。让哼唱从第一个音到最后一个音始终作为前景主钩子，紧密遵循原始音高走向、音符顺序、起音、拍点落位、脉冲、乐句节奏、重音、停顿和总时长；所有律动、和声、加花和变化都围绕这条可辨认的改编旋律展开，不能只在开头引用后就换成另一条主旋律。用切分鼓、弹性深贝斯、节奏吉他、Clavinet 和紧凑铜管替换人声，做成有记忆点的完整 Funk 片段，不要人声。",
        "negative_prompt": (
            "Raw humming, singing, speech, room noise, microphone hiss, clipping, or the unedited recording. "
            "No intro, prelude, pickup, ambient lead-in, empty opening, or delayed melody. An unrelated replacement "
            "lead melody, a lead that quotes the source only at the opening and then drifts away, new lead notes over "
            "source rests, changed note order, altered phrase entrances, displaced beats, changed pulse or accents, "
            "altered phrase rhythm, missing pauses, or stretched or compressed phrase duration. Avoid static drone, "
            "random notes, weak drums, muddy bass, harsh distortion, and excessive reverb."
        ),
        "negative_translation": "避免保留原始哼唱、歌声、说话声、房间声、麦克风嘶声、削波或未经处理的录音；不要出现前奏、铺垫、弱起、环境引入、空白开头或延迟进入主旋律；避免无关或替代性的主旋律、只在开头引用后便漂移的旋律、在原始停顿上叠加新的主音、改变音符顺序或乐句起点、挪动拍点、改变脉冲或重音、改写乐句节奏、漏掉停顿或拉伸/压缩整体乐句时长；避免静态持续音、随机音符、无力鼓组、浑浊贝斯、刺耳失真和过量混响。",
    },
    "lofi": {
        "name": "Lo-fi",
        "prompt": (
            "Start immediately at 0:00 with the supplied hummed melody; use the full short phrase and do not spend time on an intro, prelude, pickup, or ambient lead-in. "
            "Create a short, hook-first instrumental lo-fi reinterpretation of that melody. "
            "Make the supplied humming the continuous, foreground lead hook from the first note to the last: closely "
            "follow its pitch contour, note order, note entrances, beat placement, pulse, phrase rhythm, accents, "
            "pauses, and full phrase duration. Build the groove, harmony, fills, and variations around that recognizably "
            "transformed hook; enhance it, but never abandon it after the opening or substitute a different lead melody. "
            "Replace the voice with dusty pocket drums, mellow electric piano, soft bass, gentle chord color, and "
            "restrained tape texture. Make the entire 5–15 second phrase feel like a warm, memorable lo-fi idea with no vocals."
        ),
        "translation": "从 0:00 立即进入输入的哼唱旋律，不要花时间做前奏、铺垫、弱起或环境引入；用完整短乐句做器乐 Lo-fi 改编。让哼唱从第一个音到最后一个音始终作为前景主钩子，紧密遵循原始音高走向、音符顺序、起音、拍点落位、脉冲、乐句节奏、重音、停顿和总时长；所有律动、和声、加花和变化都围绕这条可辨认的改编旋律展开，不能只在开头引用后就换成另一条主旋律。用带尘感的口袋鼓、柔和电钢琴、轻柔贝斯、温和和弦色彩和克制磁带质感替换人声，做成温暖、有记忆点的完整 Lo-fi 片段，不要人声。",
        "negative_prompt": (
            "Raw humming, singing, speech, room noise, microphone hiss, clipping, or the unedited recording. "
            "No intro, prelude, pickup, ambient lead-in, empty opening, or delayed melody. An unrelated replacement "
            "lead melody, a lead that quotes the source only at the opening and then drifts away, new lead notes over "
            "source rests, changed note order, altered phrase entrances, displaced beats, changed pulse or accents, "
            "altered phrase rhythm, missing pauses, or stretched or compressed phrase duration. Avoid static drone, "
            "random notes, a muddy mix, harsh distortion, and excessive reverb."
        ),
        "negative_translation": "避免保留原始哼唱、歌声、说话声、房间声、麦克风嘶声、削波或未经处理的录音；不要出现前奏、铺垫、弱起、环境引入、空白开头或延迟进入主旋律；避免无关或替代性的主旋律、只在开头引用后便漂移的旋律、在原始停顿上叠加新的主音、改变音符顺序或乐句起点、挪动拍点、改变脉冲或重音、改写乐句节奏、漏掉停顿或拉伸/压缩整体乐句时长；避免静态持续音、随机音符、浑浊混音、刺耳失真和过量混响。",
    },
}

STYLE_ORDER = ("funk", "lofi")


def style_snapshot() -> dict[str, dict[str, str]]:
    """Return a JSON-safe copy used in every task manifest."""
    return {key: dict(value) for key, value in STYLE_PROMPTS.items()}
