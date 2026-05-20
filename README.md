# holy-moly-mcp

Universal conversion bridge that runs both as:

- an MCP stdio server (`holy-moly-mcp`)
- a FastAPI web backend + Vue 3 web UI (`holy-moly-web`)

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (for frontend development)
- `OPENAI_API_KEY` environment variable

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
OPENAI_API_KEY=your-key docker compose up --build
```

- FastAPI backend: http://localhost:8000
- Vue web UI (dev server): http://localhost:5173

## VS Code Copilot MCP configuration example (`mcp.json`)

```json
{
  "mcpServers": {
    "holy-moly": {
      "command": "uv",
      "args": [
        "run",
        "holy-moly-mcp"
      ],
      "cwd": "/absolute/path/to/holy-moly-mcp",
      "env": {
        "OPENAI_API_KEY": "${env:OPENAI_API_KEY}"
      }
    }
  }
}
```

## API endpoints

- `POST /api/v1/convert/speech-to-text` (multipart file upload + optional `mode=plain|minutes`)
- `POST /api/v1/convert/pdf-to-markdown` (placeholder for phase 2)
