"""Small, explicit mixer for experimental Control audio conditions."""

from __future__ import annotations

import numpy as np


def mix_control_contexts(
    primary: np.ndarray,
    other: np.ndarray,
    *,
    other_weight: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Blend two separated track groups by energy while preserving overall RMS.

    Both arrays use [channels, samples]. ``other_weight`` is the target energy
    share for the secondary ``other`` track group; the primary group gets the
    remaining share. A small peak limiter prevents a synthetic condition from
    exceeding normalized PCM range. This is only used by explicit A/B calls.
    """
    primary = np.asarray(primary, dtype=np.float32)
    other = np.asarray(other, dtype=np.float32)
    if primary.ndim != 2 or other.ndim != 2:
        raise ValueError("Control contexts must be [channels, samples] arrays")
    if primary.shape != other.shape:
        raise ValueError(
            f"Control contexts must have identical shapes, got {primary.shape} and {other.shape}"
        )
    if not np.isfinite(primary).all() or not np.isfinite(other).all():
        raise ValueError("Control contexts contain non-finite samples")
    if not 0.0 <= other_weight <= 1.0:
        raise ValueError("other_weight must be between 0 and 1")

    primary_rms = float(np.sqrt(np.mean(np.square(primary), dtype=np.float64)))
    other_rms = float(np.sqrt(np.mean(np.square(other), dtype=np.float64)))
    if primary_rms < 1e-8 or other_rms < 1e-8:
        raise ValueError("Cannot blend a silent Control track group")
    target_rms = float(
        np.sqrt((1.0 - other_weight) * primary_rms**2 + other_weight * other_rms**2)
    )
    mixed = target_rms * (
        np.sqrt(1.0 - other_weight) * primary / primary_rms
        + np.sqrt(other_weight) * other / other_rms
    )
    mixed_rms = float(np.sqrt(np.mean(np.square(mixed), dtype=np.float64)))
    if mixed_rms < 1e-8:
        raise ValueError("Control track groups canceled each other during mixing")
    mixed *= target_rms / mixed_rms
    peak_before_limiter = float(np.max(np.abs(mixed)))
    limiter_gain = min(1.0, 0.98 / peak_before_limiter) if peak_before_limiter else 1.0
    mixed *= limiter_gain
    return mixed.astype(np.float32, copy=False), {
        "primary_rms": primary_rms,
        "other_rms": other_rms,
        "target_rms": target_rms,
        "mixed_rms": float(np.sqrt(np.mean(np.square(mixed), dtype=np.float64))),
        "peak_before_limiter": peak_before_limiter,
        "limiter_gain": limiter_gain,
    }
