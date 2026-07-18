# holy-moly-mcp

Universal conversion bridge that runs both as:

- an MCP stdio server (`holy-moly-mcp`)
- a FastAPI web backend + Vue 3 web UI (`holy-moly-web`)
- a lightweight remote MCP client (`holy-moly-mcp-remote`) that proxies to an already-running
  `holy-moly-web` deployment instead of processing anything locally

## Repository layout

This repo is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) with two
independent, self-contained members — each has its own `pyproject.toml` and dependency set, so
installing one never pulls in the other's dependencies:

```text
server/   holy-moly-mcp + holy-moly-web — heavy local processing (faster-whisper, marker-pdf, WeasyPrint, …)
remote/   holy-moly-mcp-remote — thin stdio-to-HTTP proxy (fastmcp + httpx only)
```

The root `pyproject.toml` only declares the workspace; there is nothing to install at the root
itself.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (for frontend development)
- `ffmpeg` (for MP3 / M4A / OGG / FLAC decoding, `server` member only)

Speech transcription is handled locally via [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
No OpenAI API key required. Models are downloaded automatically from Hugging Face on first use.

## Backend setup (uv)

Sync only the member(s) you need — `--package` keeps the two dependency sets isolated:

```bash
uv sync --package holy-moly-mcp-remote    # thin proxy: fastmcp + httpx only
uv sync --package holy-moly-server        # full local stack for holy-moly-mcp / holy-moly-web
```

Run MCP stdio server:

```bash
uv run --package holy-moly-server holy-moly-mcp
```

Run web backend:

```bash
uv run --package holy-moly-server holy-moly-web
```

Run remote proxy client:

```bash
uv run --package holy-moly-mcp-remote holy-moly-mcp-remote
```

Run the `server` unit tests:

```bash
cd server && ../.venv/bin/python -m unittest discover -s tests
```

## Frontend setup (Vite + Vue 3)

For hot-reload frontend development against a separately running backend:

```bash
cd frontend
npm install
npm run dev
```

This starts the Vite dev server on http://localhost:5173, calling the API on `VITE_API_BASE_URL`
(default: same origin). For a production-like single-port build, use `npm run build` (or the
Docker Compose stack below) — the bundle is emitted to `server/dist` and served by `holy-moly-web`.

## Docker Compose stack

`docker compose up` builds a single `web` image (multi-stage: the Vue frontend is built with Node,
then its `dist/` bundle is copied next to the FastAPI backend) and serves the API, the web UI, and
the interactive API docs all from one port:

```bash
docker compose up --build
```

Optionally configure the Whisper model (default: `base`):

```bash
HOLY_MOLY_WHISPER_MODEL=small docker compose up --build
```

Available models: `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3`  
The model is downloaded automatically from Hugging Face into a named Docker volume (`whisper-cache`) and reused on subsequent starts.

- Web UI: http://localhost:8080
- Interactive API docs (Swagger UI): http://localhost:8080/docs

## VS Code MCP configuration (`.vscode/mcp.json`)

This repo ships a ready-to-use [`.vscode/mcp.json`](.vscode/mcp.json) — just open the folder in
VS Code and start the server(s) from the MCP panel (or Copilot Chat's tools picker). No manual
editing or absolute paths required; `cwd` resolves via `${workspaceFolder}`, and each server
targets its own workspace member via `uv run --package …`, so enabling one never installs the
other's dependencies.

It defines two independent servers, so you can enable either or both:

### `holy-moly-remote` — remote proxy (recommended default)

Runs locally over stdio, but proxies every tool call as an HTTP request to an already-running
`holy-moly-web` deployment (see Docker Compose section above) instead of loading
Whisper/marker-pdf/WeasyPrint locally. Only needs the `remote` member's minimal dependencies — no
GPU/CPU model downloads on the machine running VS Code. Exposes: `convert_speech_to_text`,
`convert_pdf_to_markdown`, `convert_html_to_pdf`, `list_tts_voices`, `convert_text_to_speech`.

On first start, VS Code prompts for the API URL (input `holy-moly-api-url`) and remembers it.

| Env var | Description |
|---|---|
| `HOLY_MOLY_API_URL` | Base URL of the remote `holy-moly-web` FastAPI backend |

### `holy-moly` — local in-process server

Runs `holy-moly-mcp` from the `server` member, doing transcription/PDF/TTS processing in-process.
The heavy dependencies are installed on demand the first time this server starts.

| Env var | Default | Description |
|---|---|---|
| `HOLY_MOLY_WHISPER_MODEL` | `base` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`, …) |
| `HOLY_MOLY_DEVICE` | `cpu` | Inference device (`cpu` or `cuda`) |
| `HOLY_MOLY_COMPUTE_TYPE` | `int8` | Quantisation type (`int8`, `float16`, `float32`) |

## API endpoints

`holy-moly-web` exposes an interactive OpenAPI page for documentation and manual testing at
`/docs` (Swagger UI, request bodies pre-filled with schema/example values) and `/redoc`; the raw
schema is at `/openapi.json`. Endpoints are grouped under the `Model`, `System`, `Conversion`, and
`Text-to-Speech` tags.
