"""FastAPI application for browser-based conversion workflows."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import platform
import subprocess
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.services.converter import (
    MODEL_METADATA,
    WHISPER_MODELS,
    get_active_model_name,
    get_model_cache_path,
    get_model_status,
    is_model_cached,
    process_audio,
    process_pdf_placeholder,
    start_model_loading,
    switch_model,
)

DEFAULT_UPLOAD_AUDIO_FILENAME = "upload.wav"


# ---------------------------------------------------------------------------
# Hardware helpers
# ---------------------------------------------------------------------------


def _get_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return platform.processor() or "Unknown"


def _get_gpus() -> list[dict]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            gpus = []
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpus.append(
                        {"index": int(parts[0]), "name": parts[1], "memory_mb": int(parts[2])}
                    )
            return gpus
    except Exception:  # noqa: BLE001
        pass
    return []


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    """Trigger faster-whisper model loading at server startup."""
    await start_model_loading()
    yield


app = FastAPI(title="Holy Moly Web Suite", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Model status SSE
# ---------------------------------------------------------------------------


async def _model_status_generator():
    """Yield SSE events until the model is ready or errored."""
    while True:
        status = get_model_status()
        yield f"data: {json.dumps(status)}\n\n"
        if status["phase"] in ("ready", "error"):
            break
        await asyncio.sleep(0.5)


@app.get("/api/v1/model/status")
async def model_status_stream():
    """Server-Sent Events stream of model loading progress."""
    return StreamingResponse(
        _model_status_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/model/info")
async def model_info():
    """Return the active model, device info, and enriched model list."""
    device = os.environ.get("HOLY_MOLY_DEVICE", "cpu")
    compute_type = os.environ.get("HOLY_MOLY_COMPUTE_TYPE", "int8")
    return {
        "active": get_active_model_name(),
        "device": device,
        "compute_type": compute_type,
        "models": [
            {
                "name": m,
                "cached": is_model_cached(m),
                "path": get_model_cache_path(m),
                **MODEL_METADATA.get(m, {}),
            }
            for m in WHISPER_MODELS
        ],
    }


@app.post("/api/v1/model/load")
async def model_load(model: str = Body(..., embed=True)):
    """Switch the active Whisper model."""
    try:
        await switch_model(model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "loading", "model": model}


@app.get("/api/v1/system/info")
async def system_info():
    """Return hardware information available to the server process."""
    return {
        "cpu_count": multiprocessing.cpu_count(),
        "cpu_model": _get_cpu_model(),
        "gpus": _get_gpus(),
    }


# ---------------------------------------------------------------------------
# Conversion endpoints
# ---------------------------------------------------------------------------


@app.post("/api/v1/convert/speech-to-text")
async def convert_speech_to_text_api(
    file: UploadFile = File(...),
    mode: str = Query(default="plain"),
) -> dict[str, str]:
    """Transcribe uploaded audio file into text."""
    try:
        payload = await file.read()
        transcript = await process_audio(payload, file.filename or DEFAULT_UPLOAD_AUDIO_FILENAME, mode)
        return {"result": transcript, "mode": mode}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        raise HTTPException(status_code=500, detail=f"Conversion failed: {error}") from error


@app.post("/api/v1/convert/pdf-to-markdown")
async def convert_pdf_to_markdown_api(file: UploadFile = File(...)) -> dict[str, str]:
    """Placeholder endpoint for PDF-to-Markdown conversion."""
    try:
        message = await process_pdf_placeholder(file.filename or "upload.pdf")
        return {"result": message}
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        raise HTTPException(status_code=500, detail=f"PDF conversion failed: {error}") from error


# ---------------------------------------------------------------------------
# SPA static file serving
# ---------------------------------------------------------------------------

dist_dir = Path(__file__).resolve().parents[1] / "dist"
assets_dir = dist_dir / "assets"

if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str) -> FileResponse:
    """Serve SPA assets built into /dist with index fallback."""
    requested = dist_dir / full_path
    if full_path and requested.exists() and requested.is_file():
        return FileResponse(requested)

    index = dist_dir / "index.html"
    if index.exists():
        return FileResponse(index)

    raise HTTPException(status_code=404, detail="Frontend bundle not found. Build frontend first.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import os

    host = os.getenv("HOLY_MOLY_HOST", "0.0.0.0")
    port = int(os.getenv("HOLY_MOLY_PORT", "8000"))
    uvicorn.run("src.web_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
