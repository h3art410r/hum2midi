"""Project-owned style prompts for the clean native generation path."""

from __future__ import annotations

STYLE_PROMPTS: dict[str, dict[str, str]] = {
    "funk": {
        "name": "Funk",
        "prompt": (
            "An energetic instrumental funk track with a strong bass groove, "
            "syncopated drums, rhythmic guitar, tight keyboard accents, "
            "and a memorable arrangement."
        ),
        "translation": "充满能量的器乐 Funk：强劲贝斯律动、切分鼓点、节奏吉他、紧凑键盘点缀和有记忆点的编曲。",
    },
    "lofi": {
        "name": "Lo-fi",
        "prompt": (
            "A warm, laid-back lo-fi instrumental with dusty drums, mellow keys, "
            "soft bass, subtle texture, and a memorable arrangement."
        ),
        "translation": "温暖松弛的 Lo-fi 器乐：带尘感的鼓、柔和键盘、轻柔贝斯、细微质感和有记忆点的编曲。",
    },
}

STYLE_ORDER = ("funk", "lofi")


def style_snapshot() -> dict[str, dict[str, str]]:
    """Return a JSON-safe copy used in every task manifest."""
    return {key: dict(value) for key, value in STYLE_PROMPTS.items()}
