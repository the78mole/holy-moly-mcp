"""Kokoro ONNX TTS service – multi-model management, WAV streaming, OGG save."""

from __future__ import annotations

import asyncio
import os
import struct
import sys
import threading
import uuid as _uuid_mod
from pathlib import Path
from typing import AsyncGenerator

import httpx
import numpy as np
import soundfile as sf

# ---------------------------------------------------------------------------
# Model catalogue
# ---------------------------------------------------------------------------

KOKORO_CACHE_DIR = Path(
    os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
) / "kokoro"

_GH_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

VOICES_FILENAME = "voices-v1.0.bin"
VOICES_URL = f"{_GH_RELEASE}/{VOICES_FILENAME}"

TTS_MODELS: list[dict] = [
    {
        "name": "kokoro-v1.0-int8",
        "file": "kokoro-v1.0.int8.onnx",
        "url": f"{_GH_RELEASE}/kokoro-v1.0.int8.onnx",
        "size_mb": 88,
        "precision": "int8",
        "quality": "good",
        "speed": "fast",
    },
    {
        "name": "kokoro-v1.0-fp16",
        "file": "kokoro-v1.0.fp16.onnx",
        "url": f"{_GH_RELEASE}/kokoro-v1.0.fp16.onnx",
        "size_mb": 169,
        "precision": "fp16",
        "quality": "very good",
        "speed": "medium",
    },
    {
        "name": "kokoro-v1.0",
        "file": "kokoro-v1.0.onnx",
        "url": f"{_GH_RELEASE}/kokoro-v1.0.onnx",
        "size_mb": 310,
        "precision": "f32",
        "quality": "excellent",
        "speed": "slow",
    },
]

DEFAULT_TTS_MODEL: str = os.environ.get("HOLY_MOLY_TTS_MODEL", "kokoro-v1.0-int8")
SAMPLE_RATE = 24_000
TEMP_DIR = Path(__file__).resolve().parents[2] / "temp"
MAX_TEXT_LENGTH = 2_000

# ---------------------------------------------------------------------------
# Piper TTS model catalogue (German voices)
# ---------------------------------------------------------------------------

PIPER_REPO_ID = "rhasspy/piper-voices"
PIPER_REVISION = "v1.0.0"

PIPER_MODELS: dict[str, dict] = {
    "de_DE-thorsten-medium": {
        "onnx": "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx",
        "json": "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json",
    },
    "de_DE-kerstin-low": {
        "onnx": "de/de_DE/kerstin/low/de_DE-kerstin-low.onnx",
        "json": "de/de_DE/kerstin/low/de_DE-kerstin-low.onnx.json",
    },
}

# ---------------------------------------------------------------------------
# Voice catalogue
# ---------------------------------------------------------------------------

TTS_VOICES: list[dict] = [
    # American English (Kokoro) — 9 voices
    {"id": "af_heart",    "name": "Heart (EN-US, ♀)",    "lang": "en-us"},
    {"id": "af_bella",    "name": "Bella (EN-US, ♀)",    "lang": "en-us"},
    {"id": "af_aoede",    "name": "Aoede (EN-US, ♀)",    "lang": "en-us"},
    {"id": "af_sarah",    "name": "Sarah (EN-US, ♀)",    "lang": "en-us"},
    {"id": "af_nova",     "name": "Nova (EN-US, ♀)",     "lang": "en-us"},
    {"id": "am_adam",     "name": "Adam (EN-US, ♂)",     "lang": "en-us"},
    {"id": "am_fenrir",   "name": "Fenrir (EN-US, ♂)",   "lang": "en-us"},
    {"id": "am_michael",  "name": "Michael (EN-US, ♂)",  "lang": "en-us"},
    {"id": "am_puck",     "name": "Puck (EN-US, ♂)",     "lang": "en-us"},
    # British English (Kokoro) — 4 voices
    {"id": "bf_emma",     "name": "Emma (EN-GB, ♀)",     "lang": "en-gb"},
    {"id": "bf_alice",    "name": "Alice (EN-GB, ♀)",    "lang": "en-gb"},
    {"id": "bm_george",   "name": "George (EN-GB, ♂)",   "lang": "en-gb"},
    {"id": "bm_fable",    "name": "Fable (EN-GB, ♂)",    "lang": "en-gb"},
    # Deutsch (Piper) — 2 voices
    {"id": "de_thorsten", "name": "Thorsten (DE, ♂)",    "lang": "de", "backend": "piper", "piper_model": "de_DE-thorsten-medium"},
    {"id": "de_kerstin",  "name": "Kerstin (DE, ♀)",     "lang": "de", "backend": "piper", "piper_model": "de_DE-kerstin-low"},
    # Francais (Kokoro) — 1 voice
    {"id": "ff_siwis",    "name": "Siwis (FR, ♀)",       "lang": "fr-fr"},
    # Italiano (Kokoro) — 2 voices
    {"id": "if_sara",     "name": "Sara (IT, ♀)",        "lang": "it"},
    {"id": "im_nicola",   "name": "Nicola (IT, ♂)",      "lang": "it"},
]

_VOICE_LANG: dict[str, str]        = {v["id"]: v["lang"]                     for v in TTS_VOICES}
_VOICE_BACKEND: dict[str, str]     = {v["id"]: v.get("backend", "kokoro")    for v in TTS_VOICES}
_VOICE_PIPER_MODEL: dict[str, str] = {v["id"]: v["piper_model"]              for v in TTS_VOICES if "piper_model" in v}

# ---------------------------------------------------------------------------
# Shared mutable state
# ---------------------------------------------------------------------------

_active_tts_model: str = DEFAULT_TTS_MODEL
_kokoro: object | None = None
_kokoro_lock = threading.Lock()

_piper_voices: dict[str, object] = {}
_piper_voices_lock = threading.Lock()

_tts_status: dict = {
    "phase": "idle",
    "progress": 0,
    "message": "Kein Modell geladen",
    "model": DEFAULT_TTS_MODEL,
}
_tts_status_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_tts_voices() -> list[dict]:
    return [{"id": v["id"], "name": v["name"], "lang": v["lang"]} for v in TTS_VOICES]


def _model_meta(name: str) -> dict | None:
    return next((m for m in TTS_MODELS if m["name"] == name), None)


def is_tts_model_cached(name: str) -> bool:
    meta = _model_meta(name)
    return bool(meta and (KOKORO_CACHE_DIR / meta["file"]).exists())


def get_tts_model_info() -> dict:
    """Return available TTS models with cache status and the active model name."""
    return {
        "models": [
            {**m, "cached": is_tts_model_cached(m["name"])}
            for m in TTS_MODELS
        ],
        "active": _active_tts_model,
    }


def get_tts_model_status() -> dict:
    with _tts_status_lock:
        return dict(_tts_status)


def _update_status(phase: str, progress: int = 0, message: str = "", model: str | None = None) -> None:
    with _tts_status_lock:
        _tts_status.update({"phase": phase, "progress": progress, "message": message})
        if model is not None:
            _tts_status["model"] = model


# ---------------------------------------------------------------------------
# Download helper (httpx streaming with progress)
# ---------------------------------------------------------------------------


def _download_file(url: str, dest: Path, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"[holy-moly/tts] Downloading {label} …", file=sys.stderr, flush=True)

    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=65_536):
                fh.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = min(99, int(downloaded / total * 100))
                    _update_status("loading", pct, f"Lade {label}… {pct} %")

    tmp.rename(dest)
    print(f"[holy-moly/tts] Saved: {dest}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Background model loading
# ---------------------------------------------------------------------------


def _load_model_sync(model_name: str) -> None:
    global _kokoro, _active_tts_model
    try:
        meta = _model_meta(model_name)
        if meta is None:
            raise ValueError(f"Unknown TTS model: {model_name!r}")

        voices_path = KOKORO_CACHE_DIR / VOICES_FILENAME
        onnx_path = KOKORO_CACHE_DIR / meta["file"]

        if not voices_path.exists():
            _update_status("loading", 0, "Lade Stimmdaten (27 MB)…")
            _download_file(VOICES_URL, voices_path, "Stimmdaten")

        if not onnx_path.exists():
            _update_status("loading", 0, f"Lade Modell {meta['name']} ({meta['size_mb']} MB)…")
            _download_file(meta["url"], onnx_path, meta["name"])

        _update_status("loading", 99, f"Initialisiere {meta['name']}…")

        from kokoro_onnx import Kokoro  # noqa: PLC0415

        with _kokoro_lock:
            _kokoro = Kokoro(str(onnx_path), str(voices_path))
            _active_tts_model = model_name

        _update_status("ready", 100, f"{meta['name']} bereit.", model_name)
        print(f"[holy-moly/tts] Model ready: {model_name}", file=sys.stderr, flush=True)

    except Exception as exc:  # noqa: BLE001
        _update_status("error", 0, f"Fehler: {exc}")
        print(f"[holy-moly/tts] Load error: {exc}", file=sys.stderr, flush=True)


async def start_tts_model_loading(model_name: str | None = None) -> None:
    """Trigger background loading of the TTS model (non-blocking)."""
    name = model_name or DEFAULT_TTS_MODEL
    _update_status("loading", 0, f"Starte Laden von {name}…", name)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _load_model_sync, name)


async def switch_tts_model(model_name: str) -> None:
    """Invalidate the cached Kokoro instance and load a different model."""
    global _kokoro
    if _model_meta(model_name) is None:
        raise ValueError(f"Unknown TTS model: {model_name!r}")
    with _kokoro_lock:
        _kokoro = None
    await start_tts_model_loading(model_name)


async def _wait_for_tts_ready(timeout: float = 600.0) -> None:
    """Await Kokoro model readiness after a (re)load; raises on error or timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        status = get_tts_model_status()
        if status["phase"] == "ready":
            return
        if status["phase"] == "error":
            raise RuntimeError(f"TTS model loading failed: {status['message']}")
        if loop.time() > deadline:
            raise TimeoutError("Timed out waiting for TTS model to load.")
        await asyncio.sleep(0.3)


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _make_wav_stream_header(sample_rate: int = SAMPLE_RATE) -> bytes:
    bits, channels = 16, 1
    riff = struct.pack("<4sI4s", b"RIFF", 0xFFFF_FFFF, b"WAVE")
    fmt = struct.pack(
        "<4sIHHIIHH",
        b"fmt ", 16, 1, channels, sample_rate,
        sample_rate * channels * bits // 8, channels * bits // 8, bits,
    )
    data = struct.pack("<4sI", b"data", 0xFFFF_FFFF)
    return riff + fmt + data


def _f32_to_i16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32_767).astype(np.int16).tobytes()


def _save_ogg(chunks: list[np.ndarray], task_id: str, sample_rate: int = SAMPLE_RATE) -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TEMP_DIR / f"{task_id}.ogg"
    sf.write(str(out), np.concatenate(chunks), sample_rate, format="OGG", subtype="VORBIS")
    print(f"[holy-moly/tts] OGG saved → {out}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def validate_task_id(task_id: str) -> None:
    """Raise ValueError if task_id is not a strict UUID4 (prevents path traversal)."""
    try:
        parsed = _uuid_mod.UUID(task_id, version=4)
        if str(parsed) != task_id:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid task_id: {task_id!r}")


# ---------------------------------------------------------------------------
# Piper voice loader
# ---------------------------------------------------------------------------


def _get_piper_voice(voice_id: str) -> object:
    """Return a cached PiperVoice instance, downloading the model on first use."""
    with _piper_voices_lock:
        if voice_id in _piper_voices:
            return _piper_voices[voice_id]

    # I/O outside the lock – last writer wins in a race, which is harmless.
    from piper.voice import PiperVoice  # noqa: PLC0415
    from huggingface_hub import hf_hub_download  # noqa: PLC0415

    model_name = _VOICE_PIPER_MODEL[voice_id]
    meta = PIPER_MODELS[model_name]
    print(f"[holy-moly/tts] Loading Piper voice {model_name}…", file=sys.stderr, flush=True)

    onnx_path = hf_hub_download(repo_id=PIPER_REPO_ID, filename=meta["onnx"], revision=PIPER_REVISION)
    json_path = hf_hub_download(repo_id=PIPER_REPO_ID, filename=meta["json"], revision=PIPER_REVISION)
    voice = PiperVoice.load(onnx_path, config_path=json_path, use_cuda=False)
    print(f"[holy-moly/tts] Piper voice {model_name} ready.", file=sys.stderr, flush=True)

    with _piper_voices_lock:
        _piper_voices[voice_id] = voice
    return voice


# ---------------------------------------------------------------------------
# TTS streaming
# ---------------------------------------------------------------------------


async def prepare_tts(
    text: str,
    voice_id: str,
    task_id: str,
    model: str | None = None,
) -> str:
    """Validate inputs and switch the TTS model if requested; returns the resolved backend.

    Must be awaited *before* calling :func:`stream_tts`. Async generators only start running
    their body once iterated, so raising from inside one is too late for a caller to convert
    into a clean HTTP error — validation and any model switch therefore happen here instead.

    Args:
        model: Optional Kokoro model name (e.g. ``kokoro-v1.0-fp16``) to use for this
            request. If it differs from the currently active model, the model is switched
            before synthesis. Ignored for Piper-backed (German) voices, which always use
            their own single model.
    """
    if not text.strip():
        raise ValueError("Text must not be empty.")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"Text exceeds {MAX_TEXT_LENGTH} characters.")
    if voice_id not in _VOICE_LANG:
        raise ValueError(f"Unknown voice '{voice_id}'.")
    validate_task_id(task_id)

    backend = _VOICE_BACKEND[voice_id]
    if model and backend == "kokoro" and model != _active_tts_model:
        await switch_tts_model(model)
        await _wait_for_tts_ready()
    return backend


async def stream_tts(
    text: str,
    voice_id: str,
    task_id: str,
    backend: str,
) -> AsyncGenerator[bytes, None]:
    """Async generator: WAV header + int16 PCM chunks. Saves OGG on completion.

    Call :func:`prepare_tts` first to validate inputs and resolve ``backend``.
    """
    loop = asyncio.get_event_loop()

    # ── Piper backend (German voices) ───────────────────────────────────────
    if backend == "piper":
        piper_voice = await loop.run_in_executor(None, _get_piper_voice, voice_id)
        sr = piper_voice.config.sample_rate
        yield _make_wav_stream_header(sr)
        audio_chunks = await loop.run_in_executor(
            None, lambda: list(piper_voice.synthesize(text))
        )
        f32_chunks: list[np.ndarray] = []
        for chunk in audio_chunks:
            f32 = chunk.audio_float_array
            f32_chunks.append(f32)
            yield _f32_to_i16(f32)
        if f32_chunks:
            loop.run_in_executor(None, _save_ogg, f32_chunks, task_id, sr)
        return

    # ── Kokoro backend (EN / FR / IT voices) ────────────────────────────────
    with _kokoro_lock:
        kokoro = _kokoro

    if kokoro is None:
        status = get_tts_model_status()
        if status["phase"] == "loading":
            raise RuntimeError("TTS-Modell wird noch geladen. Bitte kurz warten.")
        raise RuntimeError("Kein TTS-Modell geladen. Bitte zuerst ein Modell auswählen.")

    lang = _VOICE_LANG[voice_id]
    yield _make_wav_stream_header()

    all_chunks: list[np.ndarray] = []

    try:
        async for samples, _sr in kokoro.create_stream(text, voice=voice_id, speed=1.0, lang=lang):
            all_chunks.append(samples)
            yield _f32_to_i16(samples)
    except Exception as exc:  # noqa: BLE001
        print(f"[holy-moly/tts] Synthesis error: {exc}", file=sys.stderr, flush=True)
        raise

    if all_chunks:
        loop.run_in_executor(None, _save_ogg, all_chunks, task_id)


def get_ogg_path(task_id: str) -> Path:
    validate_task_id(task_id)
    return TEMP_DIR / f"{task_id}.ogg"

