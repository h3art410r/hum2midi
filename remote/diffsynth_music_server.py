"""Minimal Windows/CUDA HTTP worker for DiffSynth-Music Prosody control.

This module is intentionally isolated from the web app: the development
machine never imports DiffSynth or CUDA packages. Start it on the 5060Ti host
with scripts/start_diffsynth_music_server.ps1.
"""

from __future__ import annotations

import os
import gc
import inspect
import tempfile
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

import torch
import torchaudio
import soundfile as sf
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from diffsynth.core.data.operators import LoadMultiTrackAudio
from diffsynth.diffusion.template import TemplatePipeline, load_template_model
from diffsynth.pipelines.diffsynth_music import DiffSynthMusicPipeline, ModelConfig
from diffsynth.utils.music_tools import extract_prosody


MODEL_ID = os.getenv("DIFFSYNTH_MODEL_ID", "DiffSynth-Studio/DiffSynth-Music")
HOST = os.getenv("DIFFSYNTH_SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("DIFFSYNTH_SERVER_PORT", "8765"))
TOKEN = os.getenv("DIFFSYNTH_REMOTE_TOKEN", "")
WORK_DIR = Path(os.getenv("DIFFSYNTH_WORK_DIR", "runtime/diffsynth_jobs"))
WORK_DIR.mkdir(parents=True, exist_ok=True)
OFFLOAD_MODE = os.getenv("DIFFSYNTH_OFFLOAD_MODE", "cpu").strip().lower()
WORKER_BUILD = os.getenv("H2M_WORKER_BUILD", "unknown")
TEMPLATE_LAZY_LOADING = os.getenv("DIFFSYNTH_TEMPLATE_LAZY_LOADING", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
# The worker normally uses the same audio conditioning for positive and
# negative CFG branches.  Template outputs are independent of the text
# prompt, so keeping one KV cache and passing it to both branches is both
# semantically equivalent and materially safer on a 16 GB card.  Set this to
# false only when a future caller supplies distinct negative template inputs.
REUSE_IDENTICAL_TEMPLATE_CACHE = os.getenv(
    "DIFFSYNTH_REUSE_TEMPLATE_CACHE", "1"
).strip().lower() not in {"0", "false", "no", "off"}
# Each DiffSynth-Music template is a full ~8 GB DiT.  TemplatePipeline does
# not use the base pipeline's VRAM manager, so on a 16 GB card the template
# weights and their KV cache can briefly exceed physical VRAM.  In CPU mode,
# keep the template's transformer blocks on RAM and materialize one block at a
# time for its forward pass.  This is slower, but it prevents WDDM
# oversubscription while preserving the Control+Prosody path.
TEMPLATE_LAYER_CPU_OFFLOAD = os.getenv(
    "DIFFSYNTH_TEMPLATE_LAYER_CPU_OFFLOAD",
    "1" if OFFLOAD_MODE == "cpu" else "0",
).strip().lower() not in {"0", "false", "no", "off"}
QUANTIZE_TEMPLATE_KV = os.getenv(
    "DIFFSYNTH_QUANTIZE_TEMPLATE_KV",
    "1" if OFFLOAD_MODE == "cpu" else "0",
).strip().lower() not in {"0", "false", "no", "off"}

app = FastAPI(title="DiffSynth-Music Prosody Worker")
PIPE = None
TEMPLATE = None
WORKER_LOGS: deque[dict[str, object]] = deque(maxlen=1000)
WORKER_LOG_SEQ = 0


def _worker_log(event: str, request_id: str | None = None, **fields: object) -> None:
    """Emit compact, grep-friendly timings for the remote worker console."""
    global WORKER_LOG_SEQ
    WORKER_LOG_SEQ += 1
    stamp = time.strftime("%H:%M:%S")
    prefix = f"[DIFFSYNTH] {stamp} {event}"
    if request_id:
        prefix += f" request={request_id}"
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{prefix} {details}".rstrip(), flush=True)
    WORKER_LOGS.append({
        "seq": WORKER_LOG_SEQ,
        "time": stamp,
        "event": event,
        "request_id": request_id,
        "fields": {key: str(value) for key, value in fields.items()},
    })


def _vram_limit_gb() -> float | None:
    """Return a safe dynamic VRAM budget for the model pool.

    ``None`` disables DiffSynth's dynamic VRAM manager. That was useful for
    the initial DiT-resident experiment, but it lets the allocator exceed a
    16GB card while relying on driver-level migration. Keep an explicit env
    override for experiments and otherwise leave headroom for activations.
    """
    configured = os.getenv("DIFFSYNTH_VRAM_LIMIT_GB", "").strip()
    if configured:
        return float(configured)
    if OFFLOAD_MODE == "none":
        return None
    if not torch.cuda.is_available():
        return None
    _free_bytes, total_bytes = torch.cuda.mem_get_info("cuda")
    headroom = float(os.getenv("DIFFSYNTH_VRAM_HEADROOM_GB", "0.75"))
    return max(1.0, total_bytes / (1024 ** 3) - headroom)


def _release_temporary_cuda_memory() -> None:
    """Release lazily loaded template objects between conditioning phases."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _cuda_memory_fields() -> dict[str, str]:
    if not torch.cuda.is_available():
        return {}
    free, total = torch.cuda.mem_get_info("cuda")
    return {
        "allocated_gb": f"{torch.cuda.memory_allocated() / 1024**3:.2f}",
        "reserved_gb": f"{torch.cuda.memory_reserved() / 1024**3:.2f}",
        "free_gb": f"{free / 1024**3:.2f}",
        "total_gb": f"{total / 1024**3:.2f}",
    }


def _log_cuda_memory(event: str, request_id: str) -> None:
    _worker_log(event, request_id, **_cuda_memory_fields())


def _move_template_cache(value: object, device: str) -> object:
    """Move nested template KV-cache tensors without changing its structure."""
    if isinstance(value, torch.Tensor):
        return value.to(device=device)
    if isinstance(value, tuple):
        return tuple(_move_template_cache(item, device) for item in value)
    if isinstance(value, list):
        return [_move_template_cache(item, device) for item in value]
    if isinstance(value, dict):
        return {key: _move_template_cache(item, device) for key, item in value.items()}
    return value


class _QuantizedKVCache:
    """CPU/GPU resident uint8 KV cache with per-tensor BF16 dequantization."""

    def __init__(self, cache: dict[str, tuple[torch.Tensor, torch.Tensor]]):
        self._entries: dict[str, tuple[tuple[torch.Tensor, torch.Tensor], tuple[torch.Tensor, torch.Tensor]]] = {}
        for key, (k, v) in cache.items():
            self._entries[key] = (self._quantize(k), self._quantize(v))

    @staticmethod
    def _quantize(value: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        value = value.float()
        scale = value.abs().amax().clamp_min(1e-8) / 127.0
        quantized = torch.clamp(torch.round(value / scale) + 128.0, 0.0, 255.0).to(torch.uint8)
        return quantized.cpu(), scale.float().cpu()

    @staticmethod
    def _dequantize(value: tuple[torch.Tensor, torch.Tensor]) -> torch.Tensor:
        quantized, scale = value
        return (quantized.to(device="cuda", dtype=torch.float32) - 128.0).mul(
            scale.to(device="cuda", dtype=torch.float32)
        ).to(dtype=torch.bfloat16)

    def get(self, key: str, default=None):
        value = self._entries.get(key)
        if value is None:
            return default
        # Do not cache the decompressed value: the DiT consumes blocks in
        # order, and retaining them would recreate the original VRAM spike.
        return self._dequantize(value[0]), self._dequantize(value[1])

    def __len__(self) -> int:
        return len(self._entries)


class _TemplateLayerCPUWrapper(torch.nn.Module):
    """Keep one template transformer block on RAM between forward calls."""

    def __init__(self, layer: torch.nn.Module):
        super().__init__()
        # MusicKVCacheModel checks this attribute to decide which blocks
        # should return KV pairs.
        self.window_size = getattr(layer, "window_size", None)
        self.layer = layer.to(dtype=torch.bfloat16, device="cpu")

    def forward(self, *args, **kwargs):
        self.layer.to(dtype=torch.bfloat16, device="cuda")
        try:
            return self.layer(*args, **kwargs)
        finally:
            # The returned activations/KV tensors stay on CUDA; only the
            # parameters move back, so the next block can use the same budget.
            self.layer.to(dtype=torch.bfloat16, device="cpu")


def _prepare_template_model(model: torch.nn.Module) -> torch.nn.Module:
    """Move template DiT blocks to CPU and page one block to CUDA per call.

    TemplatePipeline intentionally ignores ModelConfig VRAM settings.  The
    template models are full DiffSynthMusicDiTModel instances, so wrapping
    their repeated blocks is the smallest safe way to keep Control+Prosody
    under a 16 GB card without dropping a conditioning branch.
    """
    if not TEMPLATE_LAYER_CPU_OFFLOAD or getattr(model, "_h2m_template_cpu", False):
        return model
    layers = getattr(model, "layers", None)
    if not isinstance(layers, torch.nn.ModuleList):
        return model
    for index, layer in enumerate(list(layers)):
        # load_template_model creates the template on CUDA.  Keep the small
        # root modules there, but put the repeated transformer blocks in RAM;
        # the wrapper explicitly moves one block to CUDA for its call and
        # returns it to CPU even if a forward raises.
        layers[index] = _TemplateLayerCPUWrapper(layer)
    model._h2m_template_cpu = True
    return model


def _move_template_root_to_cuda(model: torch.nn.Module) -> torch.nn.Module:
    """Move non-transformer template components without reloading all layers."""
    for name, child in model.named_children():
        if name != "layers":
            child.to(dtype=torch.bfloat16, device="cuda")
    for parameter in model.parameters(recurse=False):
        parameter.data = parameter.data.to(dtype=torch.bfloat16, device="cuda")
    for buffer in model.buffers(recurse=False):
        buffer.data = buffer.data.to(device="cuda")
    return model


def _render_with_timings(
    *,
    request_id: str,
    prompt: str,
    negative_prompt: str,
    lyrics: str,
    duration: float,
    seed: int,
    cfg_scale: float,
    steps: int,
    control_audio: torch.Tensor,
    prosody: torch.Tensor,
    input_audio: torch.Tensor | None,
    denoising_strength: float,
    control_profile: str,
) -> tuple[torch.Tensor, dict[str, float]]:
    """Run the official template/pipeline flow while exposing its phases.

    This mirrors TemplatePipeline.__call__ instead of changing DiffSynth itself.
    It keeps the exact model inputs while timing template conditioning, each
    pipeline unit, every denoise step, and VAE decode separately.
    """
    timings: dict[str, float] = {}
    template_inputs = (
        [{"model_id": 0, "audio": control_audio}, {"model_id": 1, "audio": prosody}]
        if control_profile != "prosody"
        else [{"model_id": 1, "audio": prosody}]
    )
    negative_template_inputs = (
        [{"model_id": 0, "audio": control_audio}, {"model_id": 1, "audio": prosody}]
        if control_profile != "prosody"
        else [{"model_id": 1, "audio": prosody}]
    )

    started = time.perf_counter()
    # The pipeline may retain a prepared layer from the previous request.
    # Force every base-model component out before loading an 8 GB template.
    PIPE.load_models_to_device([])
    _release_temporary_cuda_memory()
    _log_cuda_memory("CUDA_BEFORE_TEMPLATE", request_id)
    if control_profile != "prosody" and len(template_inputs) == 2:
        # Control and Prosody are both full DiT template models.  Generate one
        # cache at a time, immediately move it to RAM, and merge on CPU.  The
        # previous implementation kept both caches and the merge result on
        # CUDA at once, which is what pushed this 16 GB card past its limit.
        control_cache = TEMPLATE.call_single_side(pipe=PIPE, inputs=[template_inputs[0]])
        _worker_log("TEMPLATE_CONTROL_DONE", request_id, keys=sorted(control_cache))
        control_cache = _move_template_cache(control_cache, "cpu")
        _release_temporary_cuda_memory()
        prosody_cache = TEMPLATE.call_single_side(pipe=PIPE, inputs=[template_inputs[1]])
        _worker_log("TEMPLATE_PROSODY_DONE", request_id, keys=sorted(prosody_cache))
        prosody_cache = _move_template_cache(prosody_cache, "cpu")
        _release_temporary_cuda_memory()
        merged_cache = TEMPLATE.merge_template_cache([control_cache, prosody_cache])
        if QUANTIZE_TEMPLATE_KV and "kv_cache" in merged_cache:
            template_cache = {
                **merged_cache,
                "kv_cache": _QuantizedKVCache(merged_cache["kv_cache"]),
            }
            _worker_log("TEMPLATE_KV_QUANTIZED", request_id, blocks=len(template_cache["kv_cache"]))
        else:
            template_cache = _move_template_cache(merged_cache, "cuda")
        del control_cache, prosody_cache, merged_cache
    else:
        template_cache = TEMPLATE.call_single_side(pipe=PIPE, inputs=template_inputs)
    timings["template_positive_seconds"] = time.perf_counter() - started
    _worker_log(
        "TEMPLATE_POSITIVE_DONE", request_id,
        elapsed=f"{timings['template_positive_seconds']:.3f}s",
        keys=sorted(template_cache),
    )
    _log_cuda_memory("CUDA_AFTER_TEMPLATE_POSITIVE", request_id)
    _release_temporary_cuda_memory()
    _log_cuda_memory("CUDA_AFTER_TEMPLATE_RELEASE", request_id)
    if cfg_scale == 1.0:
        # BasePipeline skips the negative forward at CFG=1. Avoid loading and
        # running the negative template branch as well.
        negative_template_cache = {}
        timings["template_negative_seconds"] = 0.0
        _worker_log("TEMPLATE_NEGATIVE_SKIPPED", request_id, reason="cfg_scale=1")
    elif REUSE_IDENTICAL_TEMPLATE_CACHE:
        # The positive and negative branches receive the exact same control
        # and prosody tensors. Template models do not see the text prompt, so
        # their KV cache is identical; alias it instead of allocating a second
        # cache (which is enough to OOM a 16 GB card).
        negative_template_cache = template_cache
        timings["template_negative_seconds"] = 0.0
        _worker_log(
            "TEMPLATE_NEGATIVE_REUSED", request_id,
            reason="identical_control_and_prosody_inputs",
        )
        _log_cuda_memory("CUDA_AFTER_TEMPLATE_NEGATIVE_REUSE", request_id)
    else:
        started = time.perf_counter()
        negative_template_cache = TEMPLATE.call_single_side(pipe=PIPE, inputs=negative_template_inputs)
        timings["template_negative_seconds"] = time.perf_counter() - started
        _worker_log(
            "TEMPLATE_NEGATIVE_DONE", request_id,
            elapsed=f"{timings['template_negative_seconds']:.3f}s",
            keys=sorted(negative_template_cache),
        )
        _release_temporary_cuda_memory()

    kwargs: dict[str, object] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "lyrics": lyrics,
        "duration": duration,
        "seed": seed,
        "tiled": True,
        "cfg_scale": cfg_scale,
        "num_inference_steps": steps,
        "input_audio": input_audio,
        "denoising_strength": denoising_strength,
    }
    required_params = set(inspect.signature(PIPE.__call__).parameters)
    for param, value in template_cache.items():
        if param in required_params:
            kwargs[param] = value
    for param, value in negative_template_cache.items():
        negative_name = "negative_" + param
        if negative_name in required_params:
            kwargs[negative_name] = value
    _worker_log(
        "PIPE_INPUTS_READY", request_id,
        kwargs=sorted(kwargs),
        input_audio="yes" if input_audio is not None else "no",
    )
    _log_cuda_memory("CUDA_BEFORE_PIPE", request_id)

    original_unit_runner = PIPE.unit_runner
    original_vae_decode = PIPE.vae_output_to_audio
    original_load_models = PIPE.load_models_to_device
    original_model_fn = PIPE.model_fn

    def timed_unit_runner(unit, *args, **unit_kwargs):
        unit_started = time.perf_counter()
        result = original_unit_runner(unit, *args, **unit_kwargs)
        elapsed = time.perf_counter() - unit_started
        name = type(unit).__name__
        timings[f"unit_{name}_seconds"] = elapsed
        _worker_log("PIPE_UNIT_DONE", request_id, unit=name, elapsed=f"{elapsed:.3f}s")
        return result

    def timed_vae_decode(*args, **decode_kwargs):
        decode_started = time.perf_counter()
        result = original_vae_decode(*args, **decode_kwargs)
        elapsed = time.perf_counter() - decode_started
        timings["vae_decode_seconds"] = elapsed
        _worker_log("VAE_DECODE_DONE", request_id, elapsed=f"{elapsed:.3f}s", shape=tuple(result.shape))
        return result

    def timed_load_models(model_names):
        load_started = time.perf_counter()
        result = original_load_models(model_names)
        elapsed = time.perf_counter() - load_started
        _worker_log(
            "MODEL_DEVICE_SWITCH_DONE",
            request_id,
            models=list(model_names),
            elapsed=f"{elapsed:.3f}s",
            offload_mode=OFFLOAD_MODE,
        )
        return result

    def timed_model_fn(*model_args, **model_kwargs):
        forward_started = time.perf_counter()
        result = original_model_fn(*model_args, **model_kwargs)
        elapsed = time.perf_counter() - forward_started
        _worker_log(
            "DIT_FORWARD_DONE",
            request_id,
            step=model_kwargs.get("progress_id", "?"),
            elapsed=f"{elapsed:.3f}s",
        )
        return result

    def timed_progress(iterable):
        iterator = iter(iterable)
        step_index = 0
        step_started = None
        while True:
            try:
                timestep = next(iterator)
            except StopIteration:
                break
            now = time.perf_counter()
            if step_started is not None:
                elapsed = now - step_started
                timings[f"denoise_step_{step_index}_seconds"] = elapsed
                _worker_log("DENOISE_STEP_DONE", request_id, step=step_index, elapsed=f"{elapsed:.3f}s")
            step_index += 1
            step_started = time.perf_counter()
            yield timestep
        if step_started is not None:
            elapsed = time.perf_counter() - step_started
            timings[f"denoise_step_{step_index}_seconds"] = elapsed
            _worker_log("DENOISE_STEP_DONE", request_id, step=step_index, elapsed=f"{elapsed:.3f}s")

    kwargs["progress_bar_cmd"] = timed_progress
    PIPE.unit_runner = timed_unit_runner
    PIPE.vae_output_to_audio = timed_vae_decode
    PIPE.load_models_to_device = timed_load_models
    PIPE.model_fn = timed_model_fn
    try:
        model_started = time.perf_counter()
        result = PIPE(**kwargs)
        timings["pipe_total_seconds"] = time.perf_counter() - model_started
    finally:
        PIPE.unit_runner = original_unit_runner
        PIPE.vae_output_to_audio = original_vae_decode
        PIPE.load_models_to_device = original_load_models
        PIPE.model_fn = original_model_fn
    _worker_log(
        "PIPE_DONE", request_id,
        elapsed=f"{timings['pipe_total_seconds']:.3f}s",
        phases=sorted(timings),
    )
    return result, timings


def _configs():
    vram_config = {
        "offload_dtype": "disk",
        "offload_device": "disk",
        "onload_dtype": "disk",
        "onload_device": "disk",
        "preparing_dtype": torch.bfloat16,
        "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16,
        "computation_device": "cuda",
    }
    vram_config_cpu = {
        "offload_dtype": torch.bfloat16,
        "offload_device": "cpu",
        "onload_dtype": torch.bfloat16,
        "onload_device": "cpu",
        "preparing_dtype": torch.bfloat16,
        "preparing_device": "cuda",
        "computation_dtype": torch.bfloat16,
        "computation_device": "cuda",
    }
    vram_config_fp32 = {
        "offload_dtype": torch.float32,
        "offload_device": "cpu",
        "onload_dtype": torch.float32,
        "onload_device": "cpu",
        "preparing_dtype": torch.float32,
        "preparing_device": "cuda",
        "computation_dtype": torch.float32,
        "computation_device": "cuda",
    }
    if OFFLOAD_MODE == "none":
        return [
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="transformer/model.safetensors"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="conditioner/model.safetensors"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/model.safetensors"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="vae/model.safetensors"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="track_separator/model.safetensors", computation_dtype=torch.float32),
        ]
    if OFFLOAD_MODE == "dit_cuda":
        # Experimental A/B mode: keep the DiT weights on CUDA while leaving
        # the text/conditioner/VAE modules on CPU between their phases. This
        # can OOM on a 16 GB card; it is never selected implicitly.
        vram_config_dit_cuda = {
            "offload_dtype": torch.bfloat16,
            "offload_device": "cpu",
            "onload_dtype": torch.bfloat16,
            "onload_device": "cuda",
            "preparing_dtype": torch.bfloat16,
            "preparing_device": "cuda",
            "computation_dtype": torch.bfloat16,
            "computation_device": "cuda",
        }
        return [
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="transformer/model.safetensors", **vram_config_dit_cuda),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="conditioner/model.safetensors", **vram_config_cpu),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/model.safetensors", **vram_config_cpu),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="vae/model.safetensors", **vram_config_cpu),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="track_separator/model.safetensors", **vram_config_fp32),
        ]
    if OFFLOAD_MODE != "cpu":
        raise RuntimeError(f"Unknown DIFFSYNTH_OFFLOAD_MODE={OFFLOAD_MODE!r}; use cpu, dit_cuda, or none")
    return [
        # Disk offload keeps VRAM low, but DiffSynth's Windows execution path
        # deep-copies disk-backed modules during inference. The safetensors
        # handles are not deepcopy/pickle-safe, so use CPU offload for these
        # three modules on Windows and keep only the active layer on CUDA.
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="transformer/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="conditioner/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="vae/model.safetensors", **vram_config_cpu),
        ModelConfig(model_id=MODEL_ID, origin_file_pattern="track_separator/model.safetensors", **vram_config_fp32),
    ]


def load_models() -> None:
    global PIPE, TEMPLATE
    if PIPE is not None:
        return
    started = time.perf_counter()
    _worker_log("MODEL_LOAD_START", model_id=MODEL_ID)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    PIPE = DiffSynthMusicPipeline.from_pretrained(
        torch_dtype=dtype, device="cuda", model_configs=_configs(),
        tokenizer_config=ModelConfig(model_id=MODEL_ID, origin_file_pattern="text_encoder/"),
        vram_limit=_vram_limit_gb(),
    )
    TEMPLATE = TemplatePipeline.from_pretrained(
        torch_dtype=dtype, device="cuda",
        model_configs=[
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_control/"),
            ModelConfig(model_id=MODEL_ID, origin_file_pattern="template_prosody/"),
        ],
        lazy_loading=TEMPLATE_LAZY_LOADING,
    )
    if TEMPLATE_LAZY_LOADING:
        original_fetch_template_model = TEMPLATE.fetch_model

        def fetch_template_model(model_id):
            if not TEMPLATE_LAYER_CPU_OFFLOAD:
                return original_fetch_template_model(model_id)
            model_config = TEMPLATE.model_configs[model_id]
            model_config.download_if_necessary()
            model = load_template_model(
                model_config.path,
                torch_dtype=TEMPLATE.torch_dtype,
                device="cpu",
                state_dict=model_config.state_dict,
            )
            return _prepare_template_model(_move_template_root_to_cuda(model))

        TEMPLATE.fetch_model = fetch_template_model
    elif TEMPLATE.models is not None:
        TEMPLATE.models = torch.nn.ModuleList(
            [_prepare_template_model(model) for model in TEMPLATE.models]
        )
    _worker_log(
        "MODEL_LOAD_DONE",
        elapsed=f"{time.perf_counter() - started:.3f}s",
        dtype=dtype,
        vram_total_gb=f"{torch.cuda.mem_get_info('cuda')[1] / (1024 ** 3):.2f}",
        vram_limit_gb=_vram_limit_gb() if _vram_limit_gb() is not None else "none",
        templates="control,prosody",
        template_lazy_loading=TEMPLATE_LAZY_LOADING,
        reuse_template_cache=REUSE_IDENTICAL_TEMPLATE_CACHE,
        template_layer_cpu_offload=TEMPLATE_LAYER_CPU_OFFLOAD,
        quantize_template_kv=QUANTIZE_TEMPLATE_KV,
        offload_mode=OFFLOAD_MODE,
    )


@app.on_event("startup")
def startup() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this worker must run on the 5060Ti machine")
    load_models()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if PIPE is not None else "starting",
        "provider": "DiffSynth-Music",
        "control": "control+prosody",
        "templates": ["control", "prosody"],
        "offload_mode": OFFLOAD_MODE,
        "vram_limit_gb": _vram_limit_gb() if torch.cuda.is_available() else None,
        "template_lazy_loading": TEMPLATE_LAZY_LOADING,
        "build": WORKER_BUILD,
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
        "model_id": MODEL_ID,
    }


@app.get("/debug/logs")
def debug_logs(since: int = 0, limit: int = 200, request_id: str = "") -> dict[str, object]:
    """Return recent worker timing events without exposing prompts or audio."""
    limit = max(1, min(limit, 500))
    events = [
        entry for entry in WORKER_LOGS
        if int(entry["seq"]) > since and (not request_id or entry.get("request_id") == request_id)
    ]
    return {"cursor": WORKER_LOG_SEQ, "logs": events[-limit:]}


@app.post("/v1/generate")
def generate(
    audio: UploadFile = File(...),
    prompt: str = Form(...),
    duration: str = Form(""),
    seed: int = Form(101),
    control: str = Form("prosody"),
    control_profile: str = Form("control_prosody"),
    denoising_strength: str = Form(""),
    cfg_scale: str = Form(""),
    steps: str = Form(""),
    authorization: str | None = Header(default=None),
) -> FileResponse:
    # This endpoint is intentionally synchronous. FastAPI runs it in its
    # worker threadpool, keeping /health and /debug/logs responsive while the
    # CUDA/Torch inference is running.
    request_id = uuid.uuid4().hex[:10]
    request_started = time.perf_counter()
    _worker_log(
        "REQUEST_START",
        request_id,
        filename=audio.filename or "input.wav",
        control_profile=control_profile,
        cfg=cfg_scale or os.getenv("DIFFSYNTH_CFG_SCALE", "4"),
        steps=steps or os.getenv("DIFFSYNTH_STEPS", "10"),
        seed=seed,
        denoise=denoising_strength or "none",
        duration=duration or "auto",
        prompt_chars=len(prompt),
    )
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "Invalid worker token")
    if control != "prosody":
        raise HTTPException(400, "This worker currently uses the prosody endpoint")
    if control_profile not in {"prosody", "control_prosody", "anchored_low", "anchored_medium"}:
        raise HTTPException(400, "Unknown control_profile")
    load_models()
    suffix = Path(audio.filename or "input.wav").suffix or ".wav"
    input_path = Path(tempfile.mkstemp(prefix="input-", suffix=suffix, dir=WORK_DIR)[1])
    output_path = input_path.with_name(input_path.stem + "-output.wav")
    try:
        read_started = time.perf_counter()
        payload = audio.file.read()
        input_path.write_bytes(payload)
        _worker_log(
            "INPUT_SAVED",
            request_id,
            bytes=len(payload),
            elapsed=f"{time.perf_counter() - read_started:.3f}s",
        )
        # Read the normalized WAV with soundfile. New torchaudio releases
        # route load() through TorchCodec, which is unnecessary for WAV and
        # makes the worker harder to deploy on Windows.
        decode_started = time.perf_counter()
        samples, sample_rate = sf.read(str(input_path), always_2d=True, dtype="float32")
        waveform = torch.from_numpy(samples.T.copy())
        _worker_log(
            "INPUT_DECODED",
            request_id,
            sample_rate=sample_rate,
            channels=waveform.shape[0],
            samples=waveform.shape[1],
            elapsed=f"{time.perf_counter() - decode_started:.3f}s",
        )
        if sample_rate != 48000:
            resample_started = time.perf_counter()
            waveform = torchaudio.functional.resample(waveform, sample_rate, 48000)
            _worker_log(
                "INPUT_RESAMPLED",
                request_id,
                from_rate=sample_rate,
                to_rate=48000,
                samples=waveform.shape[1],
                elapsed=f"{time.perf_counter() - resample_started:.3f}s",
            )
        # Control was trained on stereo vocal inputs; Prosody extraction is
        # intentionally mono. Keep both representations instead of feeding a
        # mono tensor into the Control adapter.
        prep_started = time.perf_counter()
        control_audio = waveform
        prosody = extract_prosody(waveform.mean(dim=0, keepdim=True))
        seconds = float(duration) if duration.strip() else prosody.shape[1] / 48000
        denoise = float(denoising_strength) if denoising_strength.strip() else None
        conditioning_elapsed = time.perf_counter() - prep_started
        _worker_log(
            "CONDITIONING_READY",
            request_id,
            duration=f"{seconds:.3f}s",
            control_shape=tuple(control_audio.shape),
            prosody_shape=tuple(prosody.shape),
            denoise=denoise if denoise is not None else "none",
            elapsed=f"{conditioning_elapsed:.3f}s",
        )
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        infer_started = time.perf_counter()
        _worker_log(
            "MODEL_INFER_START",
            request_id,
            cfg=float(cfg_scale) if cfg_scale.strip() else float(os.getenv("DIFFSYNTH_CFG_SCALE", "4")),
            steps=int(steps) if steps.strip() else int(os.getenv("DIFFSYNTH_STEPS", "10")),
            templates="control,prosody" if control_profile != "prosody" else "prosody",
        )
        result, phase_timings = _render_with_timings(
            request_id=request_id,
            prompt=prompt,
            negative_prompt=PIPE.default_negative_prompt,
            lyrics="",
            duration=seconds,
            seed=seed,
            cfg_scale=float(cfg_scale) if cfg_scale.strip() else float(os.getenv("DIFFSYNTH_CFG_SCALE", "4")),
            steps=int(steps) if steps.strip() else int(os.getenv("DIFFSYNTH_STEPS", "10")),
            control_audio=control_audio,
            prosody=prosody,
            input_audio=control_audio if denoise is not None else None,
            denoising_strength=denoise if denoise is not None else 1.0,
            control_profile=control_profile,
        )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_elapsed = time.perf_counter() - infer_started
        peak_allocated = torch.cuda.max_memory_allocated() / 1024**3 if torch.cuda.is_available() else 0
        peak_reserved = torch.cuda.max_memory_reserved() / 1024**3 if torch.cuda.is_available() else 0
        _worker_log(
            "MODEL_INFER_DONE",
            request_id,
            elapsed=f"{infer_elapsed:.3f}s",
            result_shape=tuple(result.shape),
            peak_allocated_gb=f"{peak_allocated:.2f}",
            peak_reserved_gb=f"{peak_reserved:.2f}",
            pipe_seconds=f"{phase_timings.get('pipe_total_seconds', 0.0):.3f}",
            vae_decode_seconds=f"{phase_timings.get('vae_decode_seconds', 0.0):.3f}",
        )
        # Avoid torchaudio.save -> TorchCodec on newer torchaudio builds.
        save_started = time.perf_counter()
        sf.write(str(output_path), result.detach().float().cpu().numpy().T, 48000, subtype="PCM_16")
        save_elapsed = time.perf_counter() - save_started
        _worker_log(
            "AUDIO_SAVE_DONE",
            request_id,
            bytes=output_path.stat().st_size,
            elapsed=f"{save_elapsed:.3f}s",
            total=f"{time.perf_counter() - request_started:.3f}s",
        )
        total_elapsed = time.perf_counter() - request_started
        return FileResponse(
            output_path,
            media_type="audio/wav",
            filename="diffsynth-output.wav",
            headers={
                "X-DiffSynth-Request-Id": request_id,
                "X-DiffSynth-Conditioning-Seconds": f"{conditioning_elapsed:.3f}",
                "X-DiffSynth-Inference-Seconds": f"{infer_elapsed:.3f}",
                "X-DiffSynth-Save-Seconds": f"{save_elapsed:.3f}",
                "X-DiffSynth-Total-Seconds": f"{total_elapsed:.3f}",
                "X-DiffSynth-Peak-Allocated-GB": f"{peak_allocated:.3f}",
                "X-DiffSynth-Peak-Reserved-GB": f"{peak_reserved:.3f}",
            },
        )
    except Exception as exc:
        _worker_log(
            "REQUEST_ERROR",
            request_id,
            elapsed=f"{time.perf_counter() - request_started:.3f}s",
            error=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        raise HTTPException(500, f"DiffSynth generation failed: {type(exc).__name__}: {exc}") from exc
    finally:
        # FileResponse reads lazily; cleanup is intentionally deferred by the
        # OS temp directory policy rather than deleting the output too early.
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
