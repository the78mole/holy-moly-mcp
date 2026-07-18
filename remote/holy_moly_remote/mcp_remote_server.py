"""FastMCP stdio server that proxies conversion tools to a remote holy-moly-web deployment.

Unlike ``mcp_server.py`` (which loads faster-whisper/marker-pdf/WeasyPrint locally),
this server does no heavy processing itself. It runs locally over stdio but forwards
every tool call as an HTTP request to an already-running ``holy-moly-web`` instance,
configured via the ``HOLY_MOLY_API_URL`` environment variable::

    "env": {
        "HOLY_MOLY_API_URL": "http://172.22.7.224:8087"
    }
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Optional

import httpx
from fastmcp import FastMCP

DEFAULT_API_URL = "http://172.22.7.224:8087"
REQUEST_TIMEOUT = httpx.Timeout(600.0)

mcp = FastMCP("Holy-Moly-Converter (remote)")


def _api_base() -> str:
    return os.environ.get("HOLY_MOLY_API_URL", DEFAULT_API_URL).rstrip("/")


@mcp.tool()
async def convert_speech_to_text(
    file_path: Optional[str] = None,
    url: Optional[str] = None,
    mode: str = "plain",
    language: Optional[str] = None,
) -> str:
    """Transcribe speech audio to text via the remote holy-moly-web API.

    Args:
        file_path: Absolute path to a local audio file (mp3, wav, m4a, ogg, flac, webm).
        url: URL of a remote audio file. Provide exactly one of `file_path` or `url`.
        mode: Transcription mode - `plain` (default) or `minutes`.
        language: ISO 639-1 language code (`de`, `en`, `fr`, ...). Omit to auto-detect.
    """
    if bool(file_path) == bool(url):
        return "Input validation failed: provide exactly one of `file_path` or `url`."

    try:
        params = {"mode": mode}
        if language:
            params["language"] = language

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            if url:
                download = await client.get(url, follow_redirects=True)
                download.raise_for_status()
                filename = Path(url).name or "download.audio"
                files = {"file": (filename, download.content)}
            else:
                path = Path(file_path)
                if not path.is_file():
                    return f"Speech-to-text conversion failed: file not found: {file_path}"
                files = {"file": (path.name, path.read_bytes())}

            response = await client.post(
                f"{_api_base()}/api/v1/convert/speech-to-text",
                params=params,
                files=files,
            )
            response.raise_for_status()
            return response.json()["result"]
    except httpx.HTTPStatusError as error:
        return f"Speech-to-text conversion failed: {error.response.status_code} {error.response.text}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Speech-to-text conversion failed: {error}"


@mcp.tool()
async def convert_pdf_to_markdown(
    file_path: Optional[str] = None,
    url: Optional[str] = None,
    timeout: int = 0,
    workers: int = 0,
) -> str:
    """Convert a PDF to Markdown via the remote holy-moly-web API (marker-pdf).

    Args:
        file_path: Absolute path to a local PDF file.
        url: URL of a remote PDF file. Provide exactly one of `file_path` or `url`.
        timeout: Subprocess timeout in seconds on the server (0 = server default).
        workers: marker_single worker count on the server (0 = server default).
    """
    if bool(file_path) == bool(url):
        return "Input validation failed: provide exactly one of `file_path` or `url`."

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            if url:
                response = await client.post(
                    f"{_api_base()}/api/v1/convert/pdf-from-url",
                    json={"url": url, "timeout": timeout, "workers": workers},
                )
            else:
                path = Path(file_path)
                if not path.is_file():
                    return f"PDF conversion failed: file not found: {file_path}"
                files = {"file": (path.name, path.read_bytes())}
                response = await client.post(
                    f"{_api_base()}/api/v1/convert/pdf-to-markdown",
                    params={"timeout": timeout, "workers": workers},
                    files=files,
                )
            response.raise_for_status()
            return response.json()["result"]
    except httpx.HTTPStatusError as error:
        return f"PDF conversion failed: {error.response.status_code} {error.response.text}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"PDF conversion failed: {error}"


@mcp.tool()
async def convert_html_to_pdf(html: str, output_path: str) -> str:
    """Render an HTML document to PDF via the remote holy-moly-web API (WeasyPrint) and save it locally.

    Args:
        html: The HTML document to render (e.g. Markdown rendered to HTML beforehand).
        output_path: Absolute local path where the resulting PDF should be written.
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{_api_base()}/api/v1/convert/markdown-to-pdf",
                json={"html": html},
            )
            response.raise_for_status()
            Path(output_path).write_bytes(response.content)
            return f"PDF written to {output_path}"
    except httpx.HTTPStatusError as error:
        return f"PDF generation failed: {error.response.status_code} {error.response.text}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"PDF generation failed: {error}"


@mcp.tool()
async def list_tts_voices() -> str:
    """List available text-to-speech voices on the remote holy-moly-web API."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{_api_base()}/api/v1/convert/text-to-speech/models")
            response.raise_for_status()
            voices = response.json()["voices"]
            return "\n".join(f"{v['id']}: {v['name']} ({v['lang']})" for v in voices)
    except httpx.HTTPStatusError as error:
        return f"Listing voices failed: {error.response.status_code} {error.response.text}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Listing voices failed: {error}"


@mcp.tool()
async def convert_text_to_speech(text: str, voice_id: str, output_path: str) -> str:
    """Synthesise speech from text via the remote holy-moly-web API and save it locally as WAV.

    Args:
        text: The text to synthesise (max 2000 characters).
        voice_id: Voice id as returned by `list_tts_voices` (e.g. `de_thorsten`).
        output_path: Absolute local path where the resulting WAV file should be written.
    """
    try:
        task_id = os.urandom(8).hex()
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            async with client.stream(
                "GET",
                f"{_api_base()}/api/v1/convert/text-to-speech/stream",
                params={"text": text, "voice_id": voice_id, "task_id": task_id},
            ) as response:
                response.raise_for_status()
                with open(output_path, "wb") as out_file:
                    async for chunk in response.aiter_bytes():
                        out_file.write(chunk)
        return f"WAV audio written to {output_path}"
    except httpx.HTTPStatusError as error:
        return f"Text-to-speech conversion failed: {error.response.status_code} {error.response.text}"
    except Exception as error:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return f"Text-to-speech conversion failed: {error}"


def main() -> None:
    """Run the remote-proxy MCP server using stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
