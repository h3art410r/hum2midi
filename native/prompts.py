"""Project-owned prompts for the clean native generation path.

These prompts are written for short (roughly 5--15 second) hummed inputs.
Prosody carries the source melody and timing; text asks the model to replace
the raw voice with a compact instrumental arrangement.
"""

from __future__ import annotations

STYLE_PROMPTS: dict[str, dict[str, str]] = {
    "funk": {
        "name": "Funk",
        "prompt": (
            "Create a short, hook-first instrumental funk reinterpretation of the supplied hummed melody. "
            "Keep its recognizable melodic contour, note timing, phrase rhythm, and pauses as the central hook, "
            "but replace the voice with a lively pocket of syncopated drums, deep elastic bass, rhythmic guitar, "
            "clavinet, and tight brass accents. Make the short 5–15 second phrase feel like a polished, memorable "
            "funk idea with tasteful variation, and no vocals."
        ),
        "translation": "把输入的哼唱旋律做成一段短小、先出钩子的器乐 Funk。保留可辨认的旋律轮廓、音符时序、乐句节奏和停顿，把人声替换成切分鼓、弹性深贝斯、节奏吉他、Clavinet 和紧凑铜管；让 5–15 秒片段像一段有记忆点、带适度变化的完整 Funk 乐句，不要人声。",
        "negative_prompt": (
            "Raw humming, singing, speech, room noise, microphone hiss, clipping, silence, or the unedited recording. "
            "Do not lose the source phrase timing or replace it with an unrelated melody. Avoid a long intro, "
            "static drone, random notes, weak drums, muddy bass, harsh distortion, and excessive reverb."
        ),
        "negative_translation": "避免保留原始哼唱、歌声、说话声、房间声、麦克风嘶声、削波、长时间静音或未经处理的录音；不要丢失原始乐句时序，也不要换成无关旋律；避免冗长前奏、静态持续音、随机音符、无力鼓组、浑浊贝斯、刺耳失真和过量混响。",
    },
    "lofi": {
        "name": "Lo-fi",
        "prompt": (
            "Create a short, hook-first instrumental lo-fi reinterpretation of the supplied hummed melody. "
            "Keep its recognizable melodic contour, note timing, phrase rhythm, and pauses as the central hook, "
            "but replace the voice with dusty pocket drums, mellow electric piano, soft bass, gentle chord color, "
            "and restrained tape texture. Make the short 5–15 second phrase feel like a warm, memorable lo-fi "
            "idea with subtle variation, and no vocals."
        ),
        "translation": "把输入的哼唱旋律做成一段短小、先出钩子的器乐 Lo-fi。保留可辨认的旋律轮廓、音符时序、乐句节奏和停顿，把人声替换成带尘感的口袋鼓、柔和电钢琴、轻柔贝斯、温和和弦色彩和克制的磁带质感；让 5–15 秒片段像一段温暖、有记忆点、带细微变化的 Lo-fi 乐句，不要人声。",
        "negative_prompt": (
            "Raw humming, singing, speech, room noise, microphone hiss, clipping, silence, or the unedited recording. "
            "Do not lose the source phrase timing or replace it with an unrelated melody. Avoid a long intro, "
            "static drone, random notes, a muddy mix, harsh distortion, and excessive reverb."
        ),
        "negative_translation": "避免保留原始哼唱、歌声、说话声、房间声、麦克风嘶声、削波、长时间静音或未经处理的录音；不要丢失原始乐句时序，也不要换成无关旋律；避免冗长前奏、静态持续音、随机音符、浑浊混音、刺耳失真和过量混响。",
    },
}

STYLE_ORDER = ("funk", "lofi")


def style_snapshot() -> dict[str, dict[str, str]]:
    """Return a JSON-safe copy used in every task manifest."""
    return {key: dict(value) for key, value in STYLE_PROMPTS.items()}
