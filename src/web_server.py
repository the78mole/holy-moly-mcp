"""FastAPI application for browser-based conversion workflows."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.services.converter import process_audio, process_pdf_placeholder

app = FastAPI(title="Holy Moly Web Suite")
DEFAULT_UPLOAD_AUDIO_FILENAME = "upload.wav"

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


def main() -> None:
    """Run FastAPI web server with uvicorn."""
    host = os.getenv("HOLY_MOLY_HOST", "0.0.0.0")
    port = int(os.getenv("HOLY_MOLY_PORT", "8000"))
    uvicorn.run("src.web_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
