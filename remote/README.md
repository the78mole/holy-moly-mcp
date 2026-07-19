# holy-moly-mcp-remote

Lightweight stdio [MCP](https://modelcontextprotocol.io/) client that proxies conversion tool
calls to an already-running [`holy-moly-web`](https://github.com/the78mole/holy-moly-mcp) deployment
over HTTP, instead of loading faster-whisper / marker-pdf / WeasyPrint locally. Only depends on
`fastmcp` and `httpx`.

## Install

```bash
pip install holy-moly-mcp-remote
# or
uvx holy-moly-mcp-remote
```

## Configuration

| Env var | Description |
|---|---|
| `HOLY_MOLY_API_URL` | Base URL of the remote `holy-moly-web` FastAPI backend |
| `HOLY_MOLY_DEFAULT_WHISPER_MODEL` | Default speech-to-text model to request (optional; per-call `model` argument overrides it) |
| `HOLY_MOLY_DEFAULT_TTS_MODEL` | Default Kokoro TTS model to request (optional; per-call `model` argument overrides it) |

## Tools

- `convert_speech_to_text` — transcribe a local file or remote URL (`mode=plain|minutes`, optional `language`, optional `model`)
- `convert_pdf_to_markdown` — convert a local PDF or remote URL to Markdown (marker-pdf)
- `convert_html_to_pdf` — render HTML to PDF (WeasyPrint) and save it locally
- `list_tts_voices` — list available text-to-speech voices
- `convert_text_to_speech` — synthesise speech to a local WAV file (optional `model` for Kokoro voices)

## VS Code MCP configuration

```json
{
  "servers": {
    "holy-moly-remote": {
      "command": "uvx",
      "args": ["holy-moly-mcp-remote"],
      "env": {
        "HOLY_MOLY_API_URL": "http://your-holy-moly-web-host:8080"
      }
    }
  }
}
```

See the [main repository](https://github.com/the78mole/holy-moly-mcp) for the full project,
including the local in-process MCP server and the FastAPI/Vue web stack this client talks to.
