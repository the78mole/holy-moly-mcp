"""Holy Moly MCP server entry point and speech-to-text tool implementation."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from fastmcp import FastMCP
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, HttpUrl, ValidationError, model_validator

mcp = FastMCP("Holy-Moly-Converter")

ALLOWED_AUDIO_EXTENSIONS: set[str] = {".ogg", ".mp3", ".wav", ".m4a"}
MAX_DOWNLOAD_SIZE_BYTES: int = 100 * 1024 * 1024


class TranscriptionMode(str, Enum):
    """Supported transcription output modes."""

    PLAIN = "plain"
    MINUTES = "minutes"


class SpeechToTextRequest(BaseModel):
    """Validated request model for speech-to-text conversion."""

    model_config = ConfigDict(extra="forbid")

    file_path: Optional[str] = None
    url: Optional[HttpUrl] = None
    mode: TranscriptionMode = TranscriptionMode.PLAIN

    @model_validator(mode="after")
    def validate_source(self) -> "SpeechToTextRequest":
        """Ensure exactly one input source is provided and local path is absolute."""
        if bool(self.file_path) == bool(self.url):
            raise ValueError("Provide exactly one input source: `file_path` or `url`.")

        if self.file_path and not Path(self.file_path).is_absolute():
            raise ValueError("`file_path` must be an absolute path.")

        return self


class SpeechToTextService:
    """Service layer for OpenAI Whisper speech-to-text transcription."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in the environment.")
        self._client = AsyncOpenAI(api_key=self._api_key)

    async def transcribe(self, audio_file_path: Path, mode: TranscriptionMode) -> str:
        """Transcribe a supported audio file with whisper-1."""
        extension = audio_file_path.suffix.lower()
        if extension not in ALLOWED_AUDIO_EXTENSIONS:
            raise ValueError(
                f"Unsupported format '{extension or '<none>'}'. Allowed formats: "
                f"{', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}."
            )

        if not audio_file_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

        with audio_file_path.open("rb") as audio_file:
            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        text = response if isinstance(response, str) else response.text

        if mode == TranscriptionMode.MINUTES:
            print(
                "[holy-moly] `minutes` mode requested; diarization/minutes are not yet "
                "implemented. Returning plain transcription.",
                file=sys.stderr,
                flush=True,
            )

        return text


async def _download_to_temp_file(url: HttpUrl) -> Path:
    """Download a remote audio file asynchronously to a temporary location."""
    suffix = Path(url.path).suffix.lower()
    if suffix and suffix not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported URL file extension '{suffix}'. Allowed formats: "
            f"{', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}."
        )

    temp_file = tempfile.NamedTemporaryFile(
        prefix="holy-moly-",
        suffix=suffix or ".tmp",
        delete=False,
    )

    total_bytes = 0

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            async with client.stream("GET", str(url), follow_redirects=True) as response:
                response.raise_for_status()

                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue

                    total_bytes += len(chunk)
                    if total_bytes > MAX_DOWNLOAD_SIZE_BYTES:
                        raise ValueError(
                            "Downloaded file exceeds maximum allowed size of "
                            f"{MAX_DOWNLOAD_SIZE_BYTES} bytes."
                        )

                    temp_file.write(chunk)

        temp_file.close()
        return Path(temp_file.name)
    except Exception:
        temp_file.close()
        try:
            Path(temp_file.name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


@mcp.tool()
async def convert_speech_to_text(
    file_path: Optional[str] = None,
    url: Optional[HttpUrl] = None,
    mode: TranscriptionMode = TranscriptionMode.PLAIN,
) -> str:
    """Convert speech audio to text from an absolute local path or remote URL."""
    temporary_file_path: Optional[Path] = None

    try:
        request = SpeechToTextRequest(file_path=file_path, url=url, mode=mode)

        source_file_path = Path(request.file_path) if request.file_path else None
        if source_file_path is None and request.url is not None:
            temporary_file_path = await _download_to_temp_file(request.url)
            source_file_path = temporary_file_path

        if source_file_path is None:
            raise RuntimeError("No source audio file was resolved from the request.")

        service = SpeechToTextService()
        return await service.transcribe(source_file_path, request.mode)

    except ValidationError as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Input validation failed: {error}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Speech-to-text conversion failed: {error}"
    finally:
        if temporary_file_path is not None:
            try:
                temporary_file_path.unlink(missing_ok=True)
            except OSError:
                traceback.print_exc(file=sys.stderr)


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
