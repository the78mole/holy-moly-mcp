"""Core conversion service layer shared by MCP and web delivery adapters."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from faster_whisper import WhisperModel

ALLOWED_AUDIO_EXTENSIONS: set[str] = {".flac", ".m4a", ".mp3", ".mp4", ".ogg", ".wav"}
MAX_DOWNLOAD_SIZE_BYTES = 100 * 1024 * 1024
DEFAULT_AUDIO_FILENAME = "downloaded-audio.wav"
DEFAULT_WHISPER_MODEL = "base"
MODEL_LOAD_TIMEOUT_SECONDS = 600

DEFAULT_AVAILABLE_MODELS: list[str] = [
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v1", "large-v2", "large-v3",
    "large-v3-turbo",
    "turbo",
    "distil-large-v2",
    "distil-large-v3",
    "distil-large-v3.5",
    "distil-medium.en",
    "distil-small.en",
]

# Backward-compatible alias
WHISPER_MODELS = DEFAULT_AVAILABLE_MODELS

DEFAULT_AVAILABLE_LANGUAGES: list[str] = ["de", "en", "fr"]

# Static metadata per model: size, speed on CPU/GPU, quality (WER on LibriSpeech-clean)
MODEL_METADATA: dict[str, dict] = {
    "tiny":              {"params": "39 M",   "size_int8_mb": 75,   "cpu_speed": "fast",      "gpu_speed": "fast",   "quality": "fair",      "wer_pct": 8.8},
    "tiny.en":           {"params": "39 M",   "size_int8_mb": 75,   "cpu_speed": "fast",      "gpu_speed": "fast",   "quality": "fair",      "wer_pct": 7.9},
    "base":              {"params": "74 M",   "size_int8_mb": 145,  "cpu_speed": "fast",      "gpu_speed": "fast",   "quality": "good",      "wer_pct": 6.9},
    "base.en":           {"params": "74 M",   "size_int8_mb": 145,  "cpu_speed": "fast",      "gpu_speed": "fast",   "quality": "good",      "wer_pct": 6.4},
    "small":             {"params": "244 M",  "size_int8_mb": 480,  "cpu_speed": "medium",    "gpu_speed": "fast",   "quality": "good",      "wer_pct": 5.0},
    "small.en":          {"params": "244 M",  "size_int8_mb": 480,  "cpu_speed": "medium",    "gpu_speed": "fast",   "quality": "good",      "wer_pct": 4.5},
    "medium":            {"params": "769 M",  "size_int8_mb": 1500, "cpu_speed": "slow",      "gpu_speed": "medium", "quality": "very good", "wer_pct": 3.4},
    "medium.en":         {"params": "769 M",  "size_int8_mb": 1500, "cpu_speed": "slow",      "gpu_speed": "medium", "quality": "very good", "wer_pct": 3.2},
    "large-v1":          {"params": "1550 M", "size_int8_mb": 3100, "cpu_speed": "very slow", "gpu_speed": "slow",   "quality": "excellent", "wer_pct": 2.7},
    "large-v2":          {"params": "1550 M", "size_int8_mb": 3100, "cpu_speed": "very slow", "gpu_speed": "slow",   "quality": "excellent", "wer_pct": 2.5},
    "large-v3":          {"params": "1550 M", "size_int8_mb": 3100, "cpu_speed": "very slow", "gpu_speed": "slow",   "quality": "best",      "wer_pct": 2.0},
    "large-v3-turbo":    {"params": "809 M",  "size_int8_mb": 1600, "cpu_speed": "slow",      "gpu_speed": "medium", "quality": "excellent", "wer_pct": 2.1},
    "turbo":             {"params": "809 M",  "size_int8_mb": 1600, "cpu_speed": "slow",      "gpu_speed": "medium", "quality": "excellent", "wer_pct": 2.1},
    "distil-large-v2":   {"params": "756 M",  "size_int8_mb": 1400, "cpu_speed": "medium",    "gpu_speed": "fast",   "quality": "very good", "wer_pct": 2.9},
    "distil-large-v3":   {"params": "756 M",  "size_int8_mb": 1400, "cpu_speed": "medium",    "gpu_speed": "fast",   "quality": "very good", "wer_pct": 2.7},
    "distil-large-v3.5": {"params": "756 M",  "size_int8_mb": 1400, "cpu_speed": "medium",    "gpu_speed": "fast",   "quality": "very good", "wer_pct": 2.6},
    "distil-medium.en":  {"params": "394 M",  "size_int8_mb": 780,  "cpu_speed": "medium",    "gpu_speed": "fast",   "quality": "good",      "wer_pct": 3.4},
    "distil-small.en":   {"params": "166 M",  "size_int8_mb": 330,  "cpu_speed": "fast",      "gpu_speed": "fast",   "quality": "good",      "wer_pct": 4.3},
}


class TranscriptionMode(str, Enum):
    """Supported speech-to-text response modes."""

    PLAIN = "plain"
    MINUTES = "minutes"


class ModelPhase(str, Enum):
    """Lifecycle phases for the faster-whisper model."""

    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass
class _ModelState:
    phase: ModelPhase = ModelPhase.IDLE
    progress: int = 0
    message: str = "Model not started"
    active_model_name: str = ""
    instance: Optional[WhisperModel] = field(default=None, repr=False)


_state = _ModelState()
_state_lock = threading.Lock()
_load_started = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_to_stderr(message: str) -> None:
    """Write diagnostic messages to stderr to keep stdout transport-safe."""
    print(message, file=sys.stderr, flush=True)


def get_model_status() -> dict:
    """Return a JSON-serialisable snapshot of the current model state."""
    with _state_lock:
        return {
            "phase": _state.phase.value,
            "progress": _state.progress,
            "message": _state.message,
            "model": _state.active_model_name,
        }


def _update_state(
    phase: ModelPhase,
    progress: int,
    message: str,
    instance: Optional[WhisperModel] = None,
) -> None:
    with _state_lock:
        _state.phase = phase
        _state.progress = progress
        _state.message = message
        if instance is not None:
            _state.instance = instance


def _get_instance() -> Optional[WhisperModel]:
    with _state_lock:
        return _state.instance


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load_model_sync(model_name: str, device: str, compute_type: str) -> None:
    """Download and load the faster-whisper model (runs in a thread executor)."""
    try:
        with _state_lock:
            _state.active_model_name = model_name
        _update_state(
            ModelPhase.LOADING,
            10,
            f"Downloading / verifying model '{model_name}' …",
        )
        _log_to_stderr(f"[holy-moly] Initialising faster-whisper model: {model_name}")

        model = WhisperModel(model_name, device=device, compute_type=compute_type)

        _update_state(ModelPhase.READY, 100, f"Model '{model_name}' is ready", instance=model)
        _log_to_stderr(f"[holy-moly] Model '{model_name}' loaded successfully.")
    except Exception as exc:
        _update_state(ModelPhase.ERROR, 0, f"Failed to load model: {exc}")
        _log_to_stderr(f"[holy-moly] Model load failed: {exc}")


async def start_model_loading() -> None:
    """Trigger background model load – idempotent, safe to call multiple times."""
    global _load_started
    with _state_lock:
        if _load_started:
            return
        _load_started = True
        _state.phase = ModelPhase.LOADING
        _state.progress = 5
        _state.message = "Starting model initialisation …"

    model_name = os.getenv("HOLY_MOLY_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
    device = os.getenv("HOLY_MOLY_DEVICE", "cpu")
    compute_type = os.getenv("HOLY_MOLY_COMPUTE_TYPE", "int8")
    with _state_lock:
        _state.active_model_name = model_name

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _load_model_sync, model_name, device, compute_type)


def get_active_model_name() -> str:
    """Return the model name that is currently loaded or being loaded."""
    with _state_lock:
        return _state.active_model_name


def _model_hf_repo(model_name: str) -> str:
    """Map a faster-whisper model name to its HuggingFace repository path."""
    if model_name.startswith("distil-"):
        return f"distil-whisper/{model_name}"
    return f"Systran/faster-whisper-{model_name}"


def _hf_cache_dirname(hf_repo: str) -> str:
    """Convert a HuggingFace repo path to its local hub cache directory name."""
    return "models--" + hf_repo.replace("/", "--")


def is_model_cached(model_name: str) -> bool:
    """Return True when the faster-whisper model files are present in the HF cache."""
    hf_cache = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    candidate = hf_cache / _hf_cache_dirname(_model_hf_repo(model_name))
    return candidate.exists() and any(candidate.iterdir())


def get_model_cache_path(model_name: str) -> str:
    """Return the expected HuggingFace cache directory for a faster-whisper model."""
    hf_cache = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return str(hf_cache / _hf_cache_dirname(_model_hf_repo(model_name)))


def get_available_models() -> list[str]:
    """Return the ordered model list for the UI.

    Reads ``HOLY_MOLY_AVAILABLE_MODELS`` (comma-separated) from env.
    Falls back to ``DEFAULT_AVAILABLE_MODELS``.
    The startup model (``HOLY_MOLY_WHISPER_MODEL``) is always included.
    """
    env_models = os.getenv("HOLY_MOLY_AVAILABLE_MODELS", "")
    models: list[str] = (
        [m.strip() for m in env_models.split(",") if m.strip()]
        if env_models.strip()
        else list(DEFAULT_AVAILABLE_MODELS)
    )
    startup_model = os.getenv("HOLY_MOLY_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
    if startup_model not in models:
        models.insert(0, startup_model)
    return models


def get_available_languages() -> list[str]:
    """Return the ISO 639-1 language codes available in the UI.

    Reads ``HOLY_MOLY_AVAILABLE_LANGUAGES`` (comma-separated) from env.
    Default: ``de``, ``en``, ``fr``.
    """
    env_langs = os.getenv("HOLY_MOLY_AVAILABLE_LANGUAGES", "")
    if env_langs.strip():
        return [lang.strip() for lang in env_langs.split(",") if lang.strip()]
    return list(DEFAULT_AVAILABLE_LANGUAGES)


async def switch_model(model_name: str) -> None:
    """Unload the current model and load a different one."""
    global _load_started
    with _state_lock:
        _state.phase = ModelPhase.LOADING
        _state.progress = 5
        _state.message = f"Switching to model '{model_name}' …"
        _state.instance = None
        _state.active_model_name = model_name
        _load_started = True  # reset – the executor below will handle loading

    device = os.getenv("HOLY_MOLY_DEVICE", "cpu")
    compute_type = os.getenv("HOLY_MOLY_COMPUTE_TYPE", "int8")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _load_model_sync, model_name, device, compute_type)


async def _wait_for_model() -> WhisperModel:
    """Await model readiness; raises on error or timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + MODEL_LOAD_TIMEOUT_SECONDS
    while True:
        status = get_model_status()
        if status["phase"] == ModelPhase.READY.value:
            model = _get_instance()
            if model is not None:
                return model
        if status["phase"] == ModelPhase.ERROR.value:
            raise RuntimeError(f"Model loading failed: {status['message']}")
        if loop.time() > deadline:
            raise TimeoutError("Timed out waiting for model to load.")
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _normalize_mode(mode: str | TranscriptionMode) -> TranscriptionMode:
    if isinstance(mode, TranscriptionMode):
        return mode
    try:
        return TranscriptionMode(mode)
    except ValueError as error:
        raise ValueError("Invalid mode. Allowed values are 'plain' and 'minutes'.") from error


def _assert_audio_extension(filename: str) -> None:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported file format '{extension or '<none>'}'. "
            f"Allowed formats: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}."
        )


# ---------------------------------------------------------------------------
# Core transcription
# ---------------------------------------------------------------------------


def _transcribe_sync(model: WhisperModel, file_path: str, language: Optional[str] = None) -> str:
    """Run faster-whisper transcription synchronously (called in executor)."""
    segments, _info = model.transcribe(file_path, beam_size=5, language=language or None)
    return " ".join(seg.text.strip() for seg in segments)


async def process_audio(
    file_bytes: bytes,
    filename: str,
    mode: str | TranscriptionMode,
    language: Optional[str] = None,
) -> str:
    """Convert audio bytes to transcript using faster-whisper."""
    if not file_bytes:
        raise ValueError("Input audio data is empty.")

    safe_filename = Path(filename).name
    _assert_audio_extension(safe_filename)
    normalized_mode = _normalize_mode(mode)

    await start_model_loading()
    model = await _wait_for_model()

    suffix = Path(safe_filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        transcript = await asyncio.to_thread(_transcribe_sync, model, tmp_path, language or None)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if normalized_mode == TranscriptionMode.MINUTES:
        _log_to_stderr(
            "[holy-moly] 'minutes' mode: diarisation not yet implemented; returning plain text."
        )

    return transcript


async def process_audio_from_path(
    file_path: str,
    mode: str | TranscriptionMode,
    language: Optional[str] = None,
) -> str:
    """Load an absolute local file path and transcribe it."""
    path = Path(file_path)
    if not path.is_absolute():
        raise ValueError("`file_path` must be an absolute path.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    file_bytes = await asyncio.to_thread(path.read_bytes)
    return await process_audio(file_bytes=file_bytes, filename=path.name, mode=mode, language=language)


# ---------------------------------------------------------------------------
# URL download
# ---------------------------------------------------------------------------


async def download_audio_from_url(url: str) -> tuple[bytes, str]:
    """Download remote audio asynchronously with size limit."""
    parsed = urlparse(url)
    filename = Path(parsed.path).name or DEFAULT_AUDIO_FILENAME
    extension = Path(filename).suffix.lower()
    if extension and extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported URL file format '{extension}'. "
            f"Allowed formats: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}."
        )

    total_size = 0
    chunks: list[bytes] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                total_size += len(chunk)
                if total_size > MAX_DOWNLOAD_SIZE_BYTES:
                    raise ValueError(
                        f"Downloaded audio exceeds maximum allowed size of {MAX_DOWNLOAD_SIZE_BYTES} bytes."
                    )
                chunks.append(chunk)

    if total_size == 0:
        raise ValueError("Downloaded file is empty.")

    resolved_filename = filename if extension else DEFAULT_AUDIO_FILENAME
    return b"".join(chunks), resolved_filename


async def process_audio_from_url(
    url: str,
    mode: str | TranscriptionMode,
    language: Optional[str] = None,
) -> str:
    """Download a remote audio file and transcribe it."""
    file_bytes, filename = await download_audio_from_url(url)
    return await process_audio(file_bytes=file_bytes, filename=filename, mode=mode, language=language)


async def process_pdf_placeholder(filename: str) -> str:
    """Future placeholder for PDF-to-Markdown conversion."""
    return (
        "PDF-to-Markdown conversion is reserved for a future phase. "
        f"Received: {Path(filename).name}"
    )
