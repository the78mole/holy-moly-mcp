"""Speech-to-text benchmark: how long does one minute of audio take on this machine?

An admin starts a run from the web UI. For every model that is already downloaded, the
bundled sample (``assets/benchmark-de.wav``, about 12 s of German speech synthesised with the
server's own TTS) is transcribed and the wall-clock time measured. The result is stored per
(model, device, compute_type) in SQLite under ``HOLY_MOLY_DATA_DIR`` and shown in the model
card as "≈ N s je Minute Audio".

The run uses a temporary ``WhisperModel`` instance for every model except the active one
(whose loaded instance is reused), so normal transcriptions keep working while it runs. Memory
peaks at active model + the model being benchmarked. Model loading time is not part of the
measurement; the first of two passes warms the model up and only the faster pass counts.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from holy_moly_server.services import converter
from holy_moly_server.token_store import data_dir

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "assets" / "benchmark-de.wav"
PASSES = 2


@dataclass(frozen=True)
class BenchmarkResult:
    model: str
    device: str
    compute_type: str
    audio_seconds: float
    best_seconds: float
    seconds_per_audio_minute: float
    realtime_factor: float
    cpu_model: str
    cpu_count: int
    measured_at: int

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class BenchmarkStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else data_dir() / "benchmarks.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stt_benchmarks (
                model TEXT NOT NULL, device TEXT NOT NULL, compute_type TEXT NOT NULL,
                audio_seconds REAL NOT NULL, best_seconds REAL NOT NULL,
                seconds_per_audio_minute REAL NOT NULL, realtime_factor REAL NOT NULL,
                cpu_model TEXT NOT NULL, cpu_count INTEGER NOT NULL, measured_at INTEGER NOT NULL,
                PRIMARY KEY (model, device, compute_type)
            )
            """
        )

    def save(self, result: BenchmarkResult) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO stt_benchmarks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (result.model, result.device, result.compute_type, result.audio_seconds, result.best_seconds,
                 result.seconds_per_audio_minute, result.realtime_factor, result.cpu_model, result.cpu_count,
                 result.measured_at),
            )

    def get(self, model: str, device: str, compute_type: str) -> BenchmarkResult | None:
        row = self._conn.execute(
            "SELECT * FROM stt_benchmarks WHERE model=? AND device=? AND compute_type=?", (model, device, compute_type)
        ).fetchone()
        return BenchmarkResult(**dict(row)) if row else None

    def all(self) -> list[BenchmarkResult]:
        return [BenchmarkResult(**dict(r)) for r in self._conn.execute("SELECT * FROM stt_benchmarks ORDER BY model")]

    def close(self) -> None:
        self._conn.close()


_store: BenchmarkStore | None = None


def get_store() -> BenchmarkStore:
    global _store
    if _store is None:
        _store = BenchmarkStore()
    return _store


def set_store(store: BenchmarkStore | None) -> None:
    global _store
    _store = store


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def measure(transcribe: Callable[[], float], *, passes: int = PASSES) -> float:
    """Run ``transcribe`` (which returns the audio duration) ``passes`` times; return the best wall time."""
    best = float("inf")
    for _ in range(max(1, passes)):
        started = time.perf_counter()
        transcribe()
        best = min(best, time.perf_counter() - started)
    return best


def make_result(model: str, device: str, compute_type: str, audio_seconds: float, best_seconds: float) -> BenchmarkResult:
    return BenchmarkResult(
        model=model,
        device=device,
        compute_type=compute_type,
        audio_seconds=round(audio_seconds, 2),
        best_seconds=round(best_seconds, 3),
        seconds_per_audio_minute=round(best_seconds / audio_seconds * 60, 1),
        realtime_factor=round(best_seconds / audio_seconds, 3),
        cpu_model=_cpu_model(),
        cpu_count=os.cpu_count() or 0,
        measured_at=int(time.time()),
    )


def _benchmark_model_sync(model_name: str, device: str, compute_type: str) -> BenchmarkResult:
    """Load (or reuse) the model, transcribe the sample twice, keep the faster pass."""
    from faster_whisper import WhisperModel  # noqa: PLC0415

    instance = None
    if model_name == converter.get_active_model_name():
        instance = converter.get_loaded_model_instance()
    owned = instance is None
    if owned:
        instance = WhisperModel(model_name, device=device, compute_type=compute_type)
    try:
        duration_holder: dict[str, float] = {}

        def run() -> float:
            segments, info = instance.transcribe(str(SAMPLE_PATH), beam_size=5)
            for _ in segments:  # the generator does the actual work
                pass
            duration_holder["audio"] = float(info.duration)
            return float(info.duration)

        best = measure(run)
        return make_result(model_name, device, compute_type, duration_holder["audio"], best)
    finally:
        if owned:
            del instance


# ---------------------------------------------------------------------------
# Run state (one run at a time)
# ---------------------------------------------------------------------------

_run_lock = threading.Lock()
_run_state: dict = {"phase": "idle", "current": None, "done": 0, "total": 0, "results": [], "error": None, "started_at": None}


def get_run_state() -> dict:
    with _run_lock:
        return json.loads(json.dumps(_run_state))


def _set(**fields) -> None:
    with _run_lock:
        _run_state.update(fields)


def _run_sync(models: list[str], device: str, compute_type: str) -> None:
    results: list[dict] = []
    measured_by_repo: dict[str, BenchmarkResult] = {}
    try:
        for index, model in enumerate(models, 1):
            _set(phase="running", current=model, done=index - 1, total=len(models))
            repo = converter._model_hf_repo(model)
            if repo in measured_by_repo:
                # Aliases such as `turbo` and `large-v3-turbo` share one set of files: reuse the number.
                twin = measured_by_repo[repo]
                result = BenchmarkResult(**{**twin.as_dict(), "model": model})
                converter._log_to_stderr(f"[holy-moly/benchmark] {model}: alias of {twin.model}, reusing result")
            else:
                converter._log_to_stderr(f"[holy-moly/benchmark] {model} ({index}/{len(models)}) …")
                result = _benchmark_model_sync(model, device, compute_type)
                measured_by_repo[repo] = result
            get_store().save(result)
            results.append(result.as_dict())
            _set(results=list(results), done=index)
            converter._log_to_stderr(
                f"[holy-moly/benchmark] {model}: {result.seconds_per_audio_minute} s per audio minute (RTF {result.realtime_factor})"
            )
        _set(phase="done", current=None)
    except Exception as exc:  # noqa: BLE001
        converter._log_to_stderr(f"[holy-moly/benchmark] failed: {exc}")
        _set(phase="error", error=str(exc), current=None)


async def start_benchmark(models: list[str] | None = None) -> list[str]:
    """Start a run in a worker thread. Returns the list of models that will be measured."""
    if not SAMPLE_PATH.exists():
        raise RuntimeError(f"benchmark sample missing: {SAMPLE_PATH}")
    state = get_run_state()
    if state["phase"] == "running":
        raise RuntimeError("A benchmark is already running.")
    if converter.get_model_status()["phase"] == "loading":
        raise RuntimeError("A model is still loading; try again when it is ready.")
    candidates = models or [m for m in converter.get_available_models() if converter.is_model_cached(m)]
    candidates = [m for m in candidates if converter.is_model_cached(m)]
    if not candidates:
        raise ValueError("No downloaded models to benchmark.")
    device = os.getenv("HOLY_MOLY_DEVICE", "cpu")
    compute_type = os.getenv("HOLY_MOLY_COMPUTE_TYPE", "int8")
    _set(phase="running", current=None, done=0, total=len(candidates), results=[], error=None, started_at=int(time.time()))
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_sync, candidates, device, compute_type)
    return candidates


def results_for_current_hardware() -> dict[str, dict]:
    """Stored results keyed by model name for the configured device/compute type."""
    device = os.getenv("HOLY_MOLY_DEVICE", "cpu")
    compute_type = os.getenv("HOLY_MOLY_COMPUTE_TYPE", "int8")
    return {r.model: r.as_dict() for r in get_store().all() if r.device == device and r.compute_type == compute_type}
