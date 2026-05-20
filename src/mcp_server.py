"""FastMCP stdio server exposing conversion tools."""

from __future__ import annotations

import sys
import traceback
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastmcp import FastMCP
from fastmcp.server.providers.base import Provider
from pydantic import BaseModel, ConfigDict, HttpUrl, ValidationError, model_validator

from src.services.converter import (
    TranscriptionMode,
    process_audio_from_path,
    process_audio_from_url,
    process_pdf_placeholder,
    start_model_loading,
)


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


mcp = FastMCP("Holy-Moly-Converter")
mcp.add_provider(_ModelStartupProvider())


class SpeechToolRequest(BaseModel):
    """Validated MCP request payload for speech transcription."""

    model_config = ConfigDict(extra="forbid")

    file_path: Optional[str] = None
    url: Optional[HttpUrl] = None
    mode: TranscriptionMode = TranscriptionMode.PLAIN
    language: Optional[str] = None

    @model_validator(mode="after")
    def validate_source(self) -> "SpeechToolRequest":
        """Require either local file path or URL, but not both."""
        if bool(self.file_path) == bool(self.url):
            raise ValueError("Provide exactly one input source: `file_path` or `url`.")
        return self


@mcp.tool()
async def convert_speech_to_text(
    file_path: Optional[str] = None,
    url: Optional[HttpUrl] = None,
    mode: TranscriptionMode = TranscriptionMode.PLAIN,
    language: Optional[str] = None,
) -> str:
    """Convert speech audio to text from a local file path or remote URL.

    Args:
        file_path: Absolute path to a local audio file.
        url: URL of a remote audio file.
        mode: Transcription mode – ``plain`` (default) or ``minutes``.
        language: ISO 639-1 language code (e.g. ``de``, ``en``, ``fr``).
                  Omit or pass ``null`` to let the model auto-detect.
    """
    try:
        request = SpeechToolRequest(file_path=file_path, url=url, mode=mode, language=language)

        if request.file_path:
            return await process_audio_from_path(request.file_path, request.mode, language=request.language)

        if request.url:
            return await process_audio_from_url(str(request.url), request.mode, language=request.language)

        return "Speech-to-text conversion failed: no source was provided."
    except ValidationError as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Input validation failed: {error}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Speech-to-text conversion failed: {error}"


@mcp.tool()
async def convert_pdf_to_markdown(file_path: str) -> str:
    """Placeholder tool for future PDF-to-Markdown support."""
    try:
        return await process_pdf_placeholder(file_path)
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"PDF conversion failed: {error}"


def main() -> None:
    """Run the MCP server using stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
