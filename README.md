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

On first start, VS Code prompts for the API URL and default models, and remembers the answers
(reset via the MCP panel if you want to change them later).

| Env var | Description |
|---|---|
| `HOLY_MOLY_API_URL` | Base URL of the remote `holy-moly-web` FastAPI backend |
| `HOLY_MOLY_DEFAULT_WHISPER_MODEL` | Default speech-to-text model to request (blank = leave the server's active model as-is) |
| `HOLY_MOLY_DEFAULT_TTS_MODEL` | Default Kokoro TTS model to request (blank = leave the server's active model as-is) |

Both `convert_speech_to_text` and `convert_text_to_speech` also accept an optional `model`
argument per call, which overrides the env-var default for just that one request.

### `holy-moly` — local in-process server

Runs `holy-moly-mcp` from the `server` member (speech-to-text, plus a PDF-to-Markdown
placeholder), loading faster-whisper in-process. The heavy dependencies are installed on demand
the first time this server starts.

| Env var | Default | Description |
|---|---|---|
| `HOLY_MOLY_WHISPER_MODEL` | `base` | Whisper model to load at startup (`tiny`, `base`, `small`, `medium`, `large-v3`, …) |
| `HOLY_MOLY_DEVICE` | `cpu` | Inference device (`cpu` or `cuda`) |
| `HOLY_MOLY_COMPUTE_TYPE` | `int8` | Quantisation type (`int8`, `float16`, `float32`) |

`convert_speech_to_text` also accepts an optional `model` argument per call, which switches the
loaded model on the fly if it differs from the current one (adds latency for the first call after
a switch, and affects concurrent requests — it's a single shared model instance, not a pool).

## API endpoints

`holy-moly-web` exposes an interactive OpenAPI page for documentation and manual testing at
`/docs` (Swagger UI, request bodies pre-filled with schema/example values) and `/redoc`; the raw
schema is at `/openapi.json`. Endpoints are grouped under the `Model`, `System`, `Conversion`, and
`Text-to-Speech` tags.

Both conversion endpoints that have a model concept take it as an optional parameter, on top of
the global default set at startup (`HOLY_MOLY_WHISPER_MODEL` / `HOLY_MOLY_TTS_MODEL`):

- `POST /api/v1/convert/speech-to-text?model=small` — faster-whisper model for this request
- `GET /api/v1/convert/text-to-speech/stream?model=kokoro-v1.0-fp16` — Kokoro model for this
  request (ignored for Piper-backed German voices)

Switching model is not instant — the previous model is unloaded and the new one loaded (and
downloaded on first use), so the first request after a switch is slower than subsequent ones.

## Releasing `holy-moly-mcp-remote` to PyPI

[`.github/workflows/publish-remote.yml`](.github/workflows/publish-remote.yml) publishes the
`remote` member to [PyPI](https://pypi.org/project/holy-moly-mcp-remote/) automatically. It
triggers on every push to `main` that touches `remote/**`; there is no manual version bump or tag
to push — [`paulhatch/semantic-version`](https://github.com/paulhatch/semantic-version) computes
the next version from commit messages since the last `v*` tag:

| Commit message starts with | Bump |
|---|---|
| `feat!:`, `fix!:` or `refactor!:` | major |
| `feat:` | minor |
| anything else | patch (increments on every such commit) |

The workflow then patches `remote/pyproject.toml`'s version, builds the package, pushes the
`vX.Y.Z` tag, creates a matching GitHub release, and publishes to PyPI via
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) — no PyPI token stored in
the repo.

**One-time setup required on PyPI (not something this repo or I can do for you):** on
[pypi.org](https://pypi.org), add a *pending trusted publisher* for a new project named
`holy-moly-mcp-remote`, pointing at this GitHub repository, workflow file
`publish-remote.yml`, and (optionally) environment name `pypi`. If you use the environment name,
also create a matching `pypi` environment under the repo's Settings → Environments (add
protection rules there if you want a manual approval gate before publishing).
