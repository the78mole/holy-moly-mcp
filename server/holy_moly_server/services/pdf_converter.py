"""PDF-to-Markdown conversion using the marker-pdf CLI (marker_single)."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# Deployment-time defaults – override via environment variables
_DEFAULT_TIMEOUT: int = int(os.getenv("MARKER_TIMEOUT", "300"))
_DEFAULT_WORKERS: int | None = (
    int(os.getenv("MARKER_WORKERS", "0")) or None
)

_MIME: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def _embed_images(markdown: str, image_dir: Path) -> str:
    """Replace relative image paths in Markdown with base64 data URIs."""

    def _replace(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        # Skip already-embedded data URIs
        if src.startswith("data:"):
            return m.group(0)
        img_path = image_dir / src
        if not img_path.exists():
            img_path = image_dir / Path(src).name
        if img_path.exists():
            ext = img_path.suffix.lstrip(".").lower()
            mime = _MIME.get(ext, "image/png")
            b64 = base64.b64encode(img_path.read_bytes()).decode()
            return f"![{alt}](data:{mime};base64,{b64})"
        return m.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace, markdown)


def _run_marker_sync(
    pdf_path: Path,
    out_dir: Path,
    *,
    timeout: int | None = None,
    workers: int | None = None,
) -> str:
    """Invoke ``marker_single`` CLI and return the resulting Markdown string."""
    effective_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT
    effective_workers = workers if workers is not None else _DEFAULT_WORKERS
    cmd = ["marker_single", str(pdf_path), "--output_dir", str(out_dir)]
    if effective_workers:
        cmd += ["--DocumentProvider_pdftext_workers", str(effective_workers)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=effective_timeout,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, flush=True)
        tail = result.stderr[-600:] if result.stderr else "(no stderr)"
        raise RuntimeError(
            f"marker_single exited with code {result.returncode}: {tail}"
        )

    md_files = sorted(out_dir.rglob("*.md"))
    if not md_files:
        raise RuntimeError("marker_single produced no Markdown output.")

    md_path = md_files[0]
    markdown = md_path.read_text(encoding="utf-8")
    return _embed_images(markdown, md_path.parent)


async def convert_pdf_bytes(
    pdf_bytes: bytes,
    filename: str,
    *,
    timeout: int | None = None,
    workers: int | None = None,
) -> str:
    """Convert PDF *bytes* to Markdown (images embedded as base64 data URIs)."""
    if not pdf_bytes:
        raise ValueError("PDF data is empty.")
    if len(pdf_bytes) > MAX_PDF_SIZE_BYTES:
        raise ValueError(
            f"PDF exceeds maximum allowed size of {MAX_PDF_SIZE_BYTES // 1024 // 1024} MB."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        safe_name = Path(filename).name or "upload.pdf"
        pdf_path = (tmp / safe_name).with_suffix(".pdf")
        pdf_path.write_bytes(pdf_bytes)
        out_dir = tmp / "output"
        out_dir.mkdir()
        return await asyncio.to_thread(
            _run_marker_sync, pdf_path, out_dir,
            timeout=timeout, workers=workers,
        )


async def convert_pdf_from_url(
    url: str,
    *,
    timeout: int | None = None,
    workers: int | None = None,
) -> str:
    """Download a PDF from *url* and convert it to Markdown."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https URLs are supported.")

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        resp = await client.get(url, headers={"User-Agent": "holy-moly-mcp/1.0"})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not url.lower().split("?")[0].endswith(".pdf"):
            raise ValueError(
                f"URL does not appear to point to a PDF (content-type: {content_type!r})."
            )
        pdf_bytes = resp.content

    filename = Path(url.split("?")[0]).name or "download.pdf"
    return await convert_pdf_bytes(pdf_bytes, filename, timeout=timeout, workers=workers)
