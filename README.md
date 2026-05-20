# holy-moly-mcp

Universal conversion bridge that runs both as:

- an MCP stdio server (`holy-moly-mcp`)
- a FastAPI web backend + Vue 3 web UI (`holy-moly-web`)

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (for frontend development)
- `ffmpeg` (for MP3 / M4A / OGG / FLAC decoding)

Speech transcription is handled locally via [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
No OpenAI API key required. Models are downloaded automatically from Hugging Face on first use.

## Backend setup (uv)

```bash
uv sync
```

Run MCP stdio server:

```bash
uv run holy-moly-mcp
```

Run web backend:

```bash
uv run holy-moly-web
```

## Frontend setup (Vite + Vue 3)

```bash
cd frontend
npm install
npm run dev
```

## Docker Compose stack

Start API + WebUI development stack:

```bash
docker compose up --build
```

Optionally configure the Whisper model (default: `base`):

```bash
HOLY_MOLY_WHISPER_MODEL=small docker compose up --build
```

Available models: `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3`  
The model is downloaded automatically from Hugging Face into a named Docker volume (`whisper-cache`) and reused on subsequent starts.

- FastAPI backend: http://localhost:8080
- Vue web UI (dev server): http://localhost:5173

## VS Code Copilot MCP configuration example (`mcp.json`)

```json
{
  "servers": {
    "holy-moly": {
      "command": "uv",
      "args": [
        "run",
        "holy-moly-mcp"
      ],
      "cwd": "/absolute/path/to/holy-moly-mcp",
      "env": {
        "HOLY_MOLY_WHISPER_MODEL": "small",
        "HOLY_MOLY_DEVICE": "cpu",
        "HOLY_MOLY_COMPUTE_TYPE": "int8"
      }
    }
  }
}
```

| Env var | Default | Description |
|---|---|---|
| `HOLY_MOLY_WHISPER_MODEL` | `base` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`, …) |
| `HOLY_MOLY_DEVICE` | `cpu` | Inference device (`cpu` or `cuda`) |
| `HOLY_MOLY_COMPUTE_TYPE` | `int8` | Quantisation type (`int8`, `float16`, `float32`) |

## API endpoints

- `POST /api/v1/convert/speech-to-text` (multipart file upload + optional `mode=plain|minutes`)
- `POST /api/v1/convert/pdf-to-markdown` (placeholder for phase 2)
