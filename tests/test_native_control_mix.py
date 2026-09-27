import unittest

import numpy as np

from native.control_mix import mix_control_contexts


class ControlContextMixTests(unittest.TestCase):
    def test_mix_keeps_shape_and_bounded_peak(self):
        samples = np.linspace(-0.4, 0.4, 256, dtype=np.float32)
        primary = np.stack([samples, samples])
        other = np.stack([np.roll(samples, 17), np.roll(samples, 17)])

        mixed, stats = mix_control_contexts(primary, other, other_weight=0.35)

        self.assertEqual(mixed.shape, primary.shape)
        self.assertEqual(mixed.dtype, np.float32)
        self.assertLessEqual(float(np.max(np.abs(mixed))), 0.980001)
        self.assertGreater(stats["mixed_rms"], 0.0)
        self.assertAlmostEqual(stats["target_rms"], stats["primary_rms"], places=4)

    def test_zero_secondary_weight_preserves_primary(self):
        primary = np.full((2, 16), 0.25, dtype=np.float32)
        other = np.full((2, 16), -0.5, dtype=np.float32)

        mixed, _ = mix_control_contexts(primary, other, other_weight=0.0)

        np.testing.assert_allclose(mixed, primary)

    def test_rejects_misaligned_or_silent_contexts(self):
        with self.assertRaisesRegex(ValueError, "identical shapes"):
            mix_control_contexts(
                np.ones((2, 8), dtype=np.float32),
                np.ones((2, 9), dtype=np.float32),
                other_weight=0.35,
            )
        with self.assertRaisesRegex(ValueError, "silent"):
            mix_control_contexts(
                np.ones((2, 8), dtype=np.float32),
                np.zeros((2, 8), dtype=np.float32),
                other_weight=0.35,
            )

    def test_rejects_opposite_phase_cancellation(self):
        primary = np.tile(np.array([[0.25, -0.25]], dtype=np.float32), (2, 8))
        with self.assertRaisesRegex(ValueError, "canceled"):
            mix_control_contexts(primary, -primary, other_weight=0.5)


if __name__ == "__main__":
    unittest.main()
