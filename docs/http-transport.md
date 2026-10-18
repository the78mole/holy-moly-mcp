# Serving MCP over HTTP

Implementation guide for exposing the conversion tools over the MCP *Streamable HTTP*
transport, in addition to the existing stdio servers.

Everything in the "Facts" section below was verified against this checkout and against
`fastmcp==4.1.0` on 2026-10-17. Re-check it if `fastmcp` is upgraded — the `http_app()`
signature has changed between major versions.

## Why

Today every client needs a local process:

```
client --stdio--> holy-moly-mcp         (server/, heavy, loads whisper + marker locally)
client --stdio--> holy-moly-mcp-remote --HTTP--> holy-moly-web   (remote/, thin proxy)
```

The proxy exists only because clients could not speak HTTP. They can now: the Claude Code
CLI supports `--transport http`, and VS Code supports `"type": "http"` with headers. For
those clients the proxy is a pointless extra hop — it receives stdio and re-emits HTTP to a
service that is already HTTP.

Target:

```
client --HTTP--> holy-moly-web/mcp      (same container, same port, no local install)
```

No `uv tool install`, no per-machine `env` block, no version skew between the client's
installed proxy and the deployment. A URL is the whole configuration.

## Facts

| | |
|---|---|
| `fastmcp` version available | 4.1.0 (both packages depend on `fastmcp`, **unpinned**) |
| Tool definitions | `server/holy_moly_server/mcp_server.py`, `mcp = FastMCP("Holy-Moly-Converter")` (line 42) |
| FastAPI app | `server/holy_moly_server/web_server.py`, `app = FastAPI(...)` (line 116) |
| Existing lifespan | same file, lines 108–113 — loads STT and TTS models at startup |
| Entry point | `main()` at line 434, `uvicorn.run("holy_moly_server.web_server:app", ...)` |
| Container | `Dockerfile.web`, compose publishes `8080:8080`; the live deployment answers on `:8087` |

Verified `fastmcp` 4.1.0 API:

```python
FastMCP.http_app(path=None, middleware=None, json_response=None, stateless_http=None,
                 transport: Literal['http','streamable-http','sse'] = 'http',
                 event_store=None, retry_interval=None)   # -> StarletteWithLifespan
FastMCP.run(transport=None, show_banner=None, **transport_kwargs)
```

`http_app()` returns a Starlette app that exposes a single route at `path` and carries its
own `.lifespan`.

## Recommended approach: mount into the existing FastAPI app

One process, one port, one image. The tools in `mcp_server.py` are already FastMCP tools, so
nothing about them changes — only how they are served.

### 1. Pin fastmcp

In **both** `server/pyproject.toml` and `remote/pyproject.toml`:

```toml
"fastmcp>=4.1,<5",
```

Unpinned is a real risk here: `http_app()` is the part of the API that has moved most between
versions, and a silent upgrade would break the mount at container build time, not at test time.

### 2. Compose the lifespans — this is the step that bites

`web_server.py` already has a lifespan that loads the models, and `http_app()` brings its own
that starts the MCP session manager. **If the MCP lifespan is not run, the endpoint accepts
connections and then fails on the first request**, which looks like a protocol bug rather than
a wiring mistake. FastAPI takes exactly one `lifespan`, so chain them explicitly.

In `server/holy_moly_server/web_server.py`:

```python
from contextlib import AsyncExitStack, asynccontextmanager

from holy_moly_server.mcp_server import create_server

mcp = create_server(http=True)   # with auth provider and per-tool role checks
mcp_app = mcp.http_app(path="/mcp")


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    """Load STT/TTS models and start the MCP session manager."""
    async with AsyncExitStack() as stack:
        # MCP first: the session manager must be live before the first request.
        await stack.enter_async_context(mcp_app.lifespan(application))
        await start_model_loading()
        await start_tts_model_loading()
        yield


app = FastAPI(
    title="Holy Moly Web Suite",
    # ... unchanged ...
    lifespan=lifespan,
)

app.mount("/mcp-server", mcp_app)
```

The endpoint URL is then the mount path plus the app's own route: `http://<host>:<port>/mcp-server/mcp`.
Pick one and keep it — mounting at `/` instead would collide with the Vue UI that is already
served from the root.

`app = FastAPI(...)` already passes `lifespan=lifespan` (line 123), so that line stays as it
is — only the body of `lifespan` changes.

### 3. Importing mcp_server pulls in the heavy dependencies

`mcp_server.py` imports from `holy_moly_server.services.converter`, which is what loads
faster-whisper, marker-pdf and WeasyPrint. Inside the `holy-moly-web` container that is fine —
those are already installed and the models are already being loaded by the existing lifespan.
Verify that importing it at module scope does not noticeably slow container start; if it does,
move the import inside the lifespan and build the ASGI app lazily.

## Alternative: standalone HTTP mode

If mounting turns out to be awkward, `FastMCP` can serve HTTP by itself:

```python
def main() -> None:
    """Run the MCP server over Streamable HTTP."""
    host = os.environ.get("HOLY_MOLY_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("HOLY_MOLY_MCP_PORT", "8088"))
    mcp.run(transport="http", host=host, port=port)
```

This means a second process and a second port in `docker-compose.yml`. Worth it only if the
MCP endpoint should scale or be restarted independently of the web UI.

**Do not do this in `remote/`.** A proxy that receives HTTP and forwards HTTP adds a hop and
buys nothing. `remote/` should keep its stdio transport and stay exactly what it is: the
fallback for clients that cannot speak HTTP.

## Authentication

There is none today, and the current setup gets away with it because `holy-moly-web` listens
on an internal address. The moment the MCP endpoint is reachable from anywhere else, anyone
who can open the URL can run speech-to-text and PDF conversion on this machine — and
`convert_html_to_pdf` writes to a path the caller chooses.

The design and implementation live in [auth-oidc-and-tokens.md](auth-oidc-and-tokens.md):
`holy_moly_server/auth.py` builds one `MultiAuth` provider (Keycloak OIDC, static bearer
tokens, local accounts) that is used by the REST dependencies and must be passed to the
MCP HTTP app. For the mount described here, use `create_server(http=True)` from
`mcp_server.py` instead of the module-level `mcp` object: it attaches the auth provider and
the per-tool role checks, which the stdio server must not carry. Mount the provider's
well-known routes at the application root (section 5.4 of that document).

## Client configuration

**Claude Code CLI:**

```bash
claude mcp add --transport http holy-moly http://172.22.7.224:8087/mcp-server/mcp
```

**VS Code** — `.vscode/mcp.json`, alongside the existing stdio entries:

```jsonc
{
  "servers": {
    "holy-moly-http": {
      "type": "http",
      "url": "${input:holy-moly-api-url}/mcp-server/mcp"
    }
  }
}
```

The existing `holy-moly-api-url` input already holds the base URL, so no new input is needed
until authentication is added.

## Verification

Start the container, then:

```bash
# 1. Tool list — must return the five conversion tools
curl -s -X POST http://172.22.7.224:8087/mcp-server/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# 2. Handshake — FastMCP implements the full protocol, so this must succeed
curl -s -X POST http://172.22.7.224:8087/mcp-server/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

# 3. The existing REST API must still work — the mount must not shadow it
curl -s http://172.22.7.224:8087/docs -o /dev/null -w '%{http_code}\n'
```

A failing `initialize` that returns a protocol-version error means client and server disagree
on the version; FastMCP negotiates, so check that the client is current before changing server
code.

Add a test alongside `server/tests/` that builds the ASGI app and asserts the MCP route
answers — the lifespan composition is exactly the kind of wiring that breaks silently on an
unrelated refactor.

## What stays

- `server/holy_moly_server/mcp_server.py` keeps its stdio `main()`. Same tools, two transports.
- `remote/` keeps working unchanged for stdio-only clients and for anyone who prefers a local
  entry point. Its README should gain a line pointing at the HTTP endpoint as the simpler
  option where the client supports it.
- `.vscode/mcp.json` keeps the two stdio servers; the HTTP entry is an addition, not a
  replacement, until the HTTP path has proven itself.
