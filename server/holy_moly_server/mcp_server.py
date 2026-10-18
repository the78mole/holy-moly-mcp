"""FastMCP server exposing the conversion tools.

``create_server(http=False)`` builds the stdio server used by ``holy-moly-mcp``. The HTTP
transport (mounted into the FastAPI app, see ``docs/http-transport.md``) calls
``create_server(http=True)``, which attaches the shared auth provider and per-tool role checks.

The role checks must only be attached for HTTP: without a bearer token (stdio, in-memory
clients) FastMCP hides every tool that carries an ``auth`` check, so a stdio server built with
``http=True`` would expose no tools at all.

Inputs: the server only sees its own file system, so every tool that consumes a file takes
either ``file_path`` (a path on the server), ``url`` (downloaded by the server) or
``content_base64`` (the file's bytes, for small files sent by the client). Outputs that are
binary (PDF, audio) come back as MCP blobs via FastMCP's ``File``/``Audio`` helpers.
"""

from __future__ import annotations

import base64
import binascii
import struct
import sys
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastmcp import FastMCP
from fastmcp.server.auth import AuthCheck, require_roles
from fastmcp.server.providers.base import Provider
from fastmcp.utilities.types import Audio, File
from pydantic import BaseModel, ConfigDict, HttpUrl, ValidationError, model_validator

from holy_moly_server import auth as auth_module
from holy_moly_server.auth import ROLE_USER, roles_of
from holy_moly_server.services import pdf_converter, tts_local
from holy_moly_server.services.converter import (
    TranscriptionMode,
    process_audio,
    process_audio_from_path,
    process_audio_from_url,
    start_model_loading,
)

MAX_INLINE_BYTES = 50 * 1024 * 1024  # content_base64 payloads above this are refused


class _ModelStartupProvider(Provider):
    """Triggers faster-whisper model loading when the MCP session starts.

    The model name and hardware settings are read from the environment variables
    that the MCP client sets in its ``mcp.json``::

        "env": {
            "HOLY_MOLY_WHISPER_MODEL": "small",
            "HOLY_MOLY_DEVICE": "cpu",
            "HOLY_MOLY_COMPUTE_TYPE": "int8"
        }
    """

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        await start_model_loading()
        yield


class SpeechToolRequest(BaseModel):
    """Validated MCP request payload for speech transcription."""

    model_config = ConfigDict(extra="forbid")

    file_path: Optional[str] = None
    url: Optional[HttpUrl] = None
    content_base64: Optional[str] = None
    filename: Optional[str] = None
    mode: TranscriptionMode = TranscriptionMode.PLAIN
    language: Optional[str] = None
    model: Optional[str] = None

    @model_validator(mode="after")
    def validate_source(self) -> "SpeechToolRequest":
        """Require exactly one input source."""
        if sum(bool(x) for x in (self.file_path, self.url, self.content_base64)) != 1:
            raise ValueError("Provide exactly one input source: `file_path`, `url` or `content_base64`.")
        return self


def _decode_inline(content_base64: str, what: str) -> bytes:
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"content_base64 is not valid base64 ({what})") from exc
    if not data:
        raise ValueError(f"content_base64 is empty ({what})")
    if len(data) > MAX_INLINE_BYTES:
        raise ValueError(f"inline {what} exceeds {MAX_INLINE_BYTES // 1024 // 1024} MB; use `url` or `file_path`")
    return data


def _finish_wav(chunks: list[bytes]) -> bytes:
    """Join streamed WAV chunks and patch the RIFF/data sizes the streaming header left open."""
    data = b"".join(chunks)
    if len(data) < 44 or data[:4] != b"RIFF":
        return data
    body = bytearray(data)
    struct.pack_into("<I", body, 4, len(body) - 8)
    struct.pack_into("<I", body, 40, len(body) - 44)
    return bytes(body)


# ---------------------------------------------------------------------------
# Tool implementations (plain functions; registered by ``create_server``)
# ---------------------------------------------------------------------------


async def convert_speech_to_text(
    file_path: Optional[str] = None,
    url: Optional[HttpUrl] = None,
    content_base64: Optional[str] = None,
    filename: Optional[str] = None,
    mode: TranscriptionMode = TranscriptionMode.PLAIN,
    language: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Convert speech audio to text (faster-whisper).

    Args:
        file_path: Absolute path to an audio file on the server.
        url: URL of a remote audio file (mp3, wav, m4a, ogg, flac, webm).
        content_base64: The audio file's bytes, base64-encoded, for small files sent directly.
        filename: Name with extension for `content_base64` (e.g. ``memo.mp3``); decides the decoder.
        mode: Transcription mode – ``plain`` (default) or ``minutes``.
        language: ISO 639-1 language code (e.g. ``de``, ``en``, ``fr``). Omit to auto-detect.
        model: faster-whisper model name (``tiny``, ``base``, ``small``, ``medium``, ``large-v3``,
               ``large-v3-turbo``) for this request. Switches the loaded model if it differs
               from the active one. Omit to keep using whichever model is already active.
    """
    try:
        request = SpeechToolRequest(
            file_path=file_path, url=url, content_base64=content_base64, filename=filename,
            mode=mode, language=language, model=model,
        )
        if request.file_path:
            return await process_audio_from_path(
                request.file_path, request.mode, language=request.language, model=request.model
            )
        if request.url:
            return await process_audio_from_url(
                str(request.url), request.mode, language=request.language, model=request.model
            )
        data = _decode_inline(request.content_base64 or "", "audio")
        return await process_audio(
            data, request.filename or "upload.wav", request.mode, language=request.language, model=request.model
        )
    except ValidationError as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Input validation failed: {error}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Speech-to-text conversion failed: {error}"


async def convert_pdf_to_markdown(
    file_path: Optional[str] = None,
    url: Optional[HttpUrl] = None,
    content_base64: Optional[str] = None,
    filename: Optional[str] = None,
    timeout: int = 0,
    workers: int = 0,
) -> str:
    """Convert a PDF to Markdown (marker-pdf; images embedded as data URIs).

    Args:
        file_path: Absolute path to a PDF on the server.
        url: URL of a remote PDF.
        content_base64: The PDF's bytes, base64-encoded, for small files sent directly.
        filename: Name for `content_base64` (default ``upload.pdf``).
        timeout: Conversion timeout in seconds (0 = server default).
        workers: marker worker count (0 = server default).
    """
    try:
        if sum(bool(x) for x in (file_path, url, content_base64)) != 1:
            return "Input validation failed: provide exactly one of `file_path`, `url` or `content_base64`."
        if url:
            return await pdf_converter.convert_pdf_from_url(str(url), timeout=timeout or None, workers=workers or None)
        if file_path:
            path = Path(file_path)
            if not path.is_absolute() or not path.is_file():
                return f"PDF conversion failed: file not found on the server: {file_path}"
            data, name = path.read_bytes(), path.name
        else:
            data, name = _decode_inline(content_base64 or "", "PDF"), filename or "upload.pdf"
        return await pdf_converter.convert_pdf_bytes(data, name, timeout=timeout or None, workers=workers or None)
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"PDF conversion failed: {error}"


async def convert_html_to_pdf(html: str, filename: str = "document.pdf") -> File | str:
    """Render an HTML document to PDF (WeasyPrint) and return the PDF as a file blob.

    Args:
        html: The HTML document to render (e.g. Markdown rendered to HTML beforehand).
        filename: Name for the returned file.
    """
    if not html.strip():
        return "PDF generation failed: `html` is empty."
    try:
        import asyncio  # noqa: PLC0415

        from holy_moly_server.services.md_to_pdf import convert_html_to_pdf as render  # noqa: PLC0415

        pdf_bytes = await asyncio.to_thread(render, html)
        return File(data=pdf_bytes, format="pdf", name=Path(filename).name or "document.pdf")
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"PDF generation failed: {error}"


async def list_tts_voices() -> str:
    """List the available text-to-speech voices (id, name, language)."""
    voices = tts_local.get_tts_voices()
    return "\n".join(f"{v['id']}: {v['name']} ({v['lang']})" for v in voices)


async def convert_text_to_speech(text: str, voice_id: str, model: Optional[str] = None) -> Audio | str:
    """Synthesise speech from text and return WAV audio as a blob.

    Args:
        text: The text to synthesise (max 2000 characters).
        voice_id: Voice id as returned by `list_tts_voices` (e.g. ``de_thorsten``).
        model: Kokoro TTS model (e.g. ``kokoro-v1.0-fp16``); ignored for Piper (German) voices.
    """
    try:
        task_id = uuid.uuid4().hex
        backend = await tts_local.prepare_tts(text, voice_id, task_id, model=model or None)
        chunks = [chunk async for chunk in tts_local.stream_tts(text, voice_id, task_id, backend)]
        return Audio(data=_finish_wav(chunks), format="wav")
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Text-to-speech conversion failed: {error}"


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

#: Every tool requires the user role over HTTP. Admin-only operations (model switching,
#: deleting, benchmarking) stay REST-only.
_USER_TOOLS = (
    convert_speech_to_text,
    convert_pdf_to_markdown,
    convert_html_to_pdf,
    list_tts_voices,
    convert_text_to_speech,
)


def create_server(http: bool = False) -> FastMCP:
    """Build the FastMCP server.

    Args:
        http: ``True`` when the server will be served over HTTP. Attaches the shared auth
              provider and requires the ``holy-moly-user`` role on every tool. ``False`` for
              stdio, where the process runs locally under the caller's own account.
    """
    checks: list[AuthCheck] | None = None
    provider = None
    if http and auth_module.auth is not None:
        provider = auth_module.auth
        checks = [require_roles(ROLE_USER, extract=roles_of)]

    server = FastMCP("Holy-Moly-Converter", auth=provider)
    server.add_provider(_ModelStartupProvider())
    for tool in _USER_TOOLS:
        server.tool(tool, auth=checks)
    return server


#: stdio server instance (kept for backwards compatibility with ``from ... import mcp``).
mcp = create_server(http=False)


def main() -> None:
    """Run the MCP server using stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
