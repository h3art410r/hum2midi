# DiffSynth-Music BPM and key metadata A/B

## Motivation

The official `DiffSynthMusicPipeline` accepts `bpm`, `timesignature`, and `keyscale`. In the current DiffSynth-Music source, omitted values are inserted into the text condition as BPM 100, time signature 4, and B minor. The music-model documentation also lists these metadata inputs. The Worker had been omitting them, so all generations were silently conditioned on B minor even when the humming may be in another key.

Official sources:

- [DiffSynth-Music Quick Start and input metadata](https://diffsynth-studio-doc.readthedocs.io/en/latest/Model_Details/DiffSynth-Music.html)
- [DiffSynthMusicPipeline implementation](https://github.com/modelscope/DiffSynth-Studio/blob/main/diffsynth/pipelines/diffsynth_music.py), especially `DiffSynthMusic_PromptEmbedder.process`, where missing metadata becomes 100 BPM, 4/4, and B minor and is encoded in the text prompt.
- [TemplatePipeline implementation](https://github.com/modelscope/DiffSynth-Studio/blob/main/diffsynth/diffusion/template.py), which forwards its extra keyword arguments into the official base pipeline.

## Fixed-input experiment

For the 10.516-second humming at `native/static/official/debug_recent_input.wav`, a tempo estimate from the onset envelope is about 103 BPM. A chroma-profile key estimate favors E minor (correlation 0.395), followed by its relative major G (0.337), so the key result is suggestive rather than conclusive. This is enough to test the official metadata field, not enough to change production defaults.

The experiment changes only `keyscale` from the implicit B-minor default to E minor. It keeps the source file, Control + Prosody, prompt, seed 42, CFG 4.5, steps 50, and the default tempo/time signature fixed. This isolates whether the model's hidden B-minor metadata conflicts with the hummed melody. The output is a listening candidate and remains outside the formal route.

The Worker now accepts optional `bpm`, `keyscale`, and `timesignature` form values. Omission preserves the prior official defaults; explicit A/B requests log and return the chosen metadata. The keyscale run is in `scripts/run_funk_keyscale_em.py` and its output/metrics will be recorded in the official experiment manifest after deployment completes.
