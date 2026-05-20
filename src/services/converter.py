"""Core conversion service layer shared by MCP and web delivery adapters."""

from __future__ import annotations

import asyncio
import io
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI

ALLOWED_AUDIO_EXTENSIONS: set[str] = {".m4a", ".mp3", ".ogg", ".wav"}
MAX_DOWNLOAD_SIZE_BYTES = 100 * 1024 * 1024


class TranscriptionMode(str, Enum):
    """Supported speech-to-text response modes."""

    PLAIN = "plain"
    MINUTES = "minutes"


def _log_to_stderr(message: str) -> None:
    """Write diagnostic messages to stderr to keep stdout transport-safe."""
    print(message, file=sys.stderr, flush=True)


def _normalize_mode(mode: str | TranscriptionMode) -> TranscriptionMode:
    """Convert incoming mode values into the mode enum."""
    if isinstance(mode, TranscriptionMode):
        return mode

    try:
        return TranscriptionMode(mode)
    except ValueError as error:
        raise ValueError("Invalid mode. Allowed values are 'plain' and 'minutes'.") from error


def _assert_audio_extension(filename: str) -> None:
    """Validate that the filename extension is one of the supported audio formats."""
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported file format '{extension or '<none>'}'. "
            f"Allowed formats: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}."
        )


def _openai_client(api_key: Optional[str] = None) -> AsyncOpenAI:
    """Create an authenticated OpenAI async client instance."""
    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    if not resolved_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment.")
    return AsyncOpenAI(api_key=resolved_key)


async def process_audio(file_bytes: bytes, filename: str, mode: str | TranscriptionMode) -> str:
    """Convert audio bytes to transcript using OpenAI Whisper."""
    if not file_bytes:
        raise ValueError("Input audio data is empty.")

    safe_filename = Path(filename).name
    _assert_audio_extension(safe_filename)

    normalized_mode = _normalize_mode(mode)
    if normalized_mode == TranscriptionMode.MINUTES:
        _log_to_stderr(
            "[holy-moly] 'minutes' mode requested. "
            "Diarization/minutes output is not implemented yet; returning plain text."
        )

    payload = io.BytesIO(file_bytes)
    payload.name = safe_filename

    client = _openai_client()
    response = await client.audio.transcriptions.create(model="whisper-1", file=payload)

    if isinstance(response, str):
        return response
    return response.text


async def process_audio_from_path(file_path: str, mode: str | TranscriptionMode) -> str:
    """Load an absolute local file path and transcribe it."""
    path = Path(file_path)
    if not path.is_absolute():
        raise ValueError("`file_path` must be an absolute path.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    file_bytes = await asyncio.to_thread(path.read_bytes)
    return await process_audio(file_bytes=file_bytes, filename=path.name, mode=mode)


async def download_audio_from_url(url: str) -> tuple[bytes, str]:
    """Download remote audio input asynchronously with basic safety limits."""
    parsed = urlparse(url)
    filename = Path(parsed.path).name or "downloaded-audio.wav"
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
                        "Downloaded audio exceeds maximum allowed size of "
                        f"{MAX_DOWNLOAD_SIZE_BYTES} bytes."
                    )
                chunks.append(chunk)

    if total_size == 0:
        raise ValueError("Downloaded file is empty.")

    if extension:
        resolved_filename = filename
    else:
        resolved_filename = "downloaded-audio.wav"

    return b"".join(chunks), resolved_filename


async def process_audio_from_url(url: str, mode: str | TranscriptionMode) -> str:
    """Download a remote audio file and transcribe it."""
    file_bytes, filename = await download_audio_from_url(url)
    return await process_audio(file_bytes=file_bytes, filename=filename, mode=mode)


async def process_pdf_placeholder(filename: str) -> str:
    """Future placeholder for PDF-to-Markdown conversion."""
    return (
        "PDF-to-Markdown conversion is reserved for a future phase. "
        f"Received file: {Path(filename).name}"
    )
