# MCP over HTTP

Status 2026-10-18: implemented. `holy-moly-web` serves the conversion tools as an MCP server over
Streamable HTTP at `/mcp-server/mcp`, next to the REST API and the web UI, in the same container
and on the same port. Authentication is shared with the REST API; see
[auth-oidc-and-tokens.md](auth-oidc-and-tokens.md).

```text
client --HTTP--> holy-moly-web/mcp-server/mcp      (no local install; a URL plus a token is the configuration)
client --stdio--> holy-moly-mcp                    (server/, loads whisper locally; unchanged)
client --stdio--> holy-moly-mcp-remote --HTTP-->   holy-moly-web REST API (remote/, for stdio-only clients)
```

## How it is wired

- `mcp_server.create_server(http=True)` builds the FastMCP server with the shared auth provider and a
  `holy-moly-user` role check on every tool. The stdio entry point uses `create_server(http=False)`:
  FastMCP hides tools that carry an `auth` check when no bearer token is in the request context, so a
  stdio server built with checks would expose nothing.
- `mcp_http.mount_mcp(app, server)` calls `server.http_app(path="/mcp")`, mounts it at `/mcp-server`
  and inserts the mount ahead of the SPA catch-all route (`/{full_path:path}`), which would otherwise
  answer every GET.
- The auth provider's RFC 9728 route lives inside the sub-app and would end up under
  `/mcp-server/.well-known/...`; `mount_mcp` adds `server.auth.get_well_known_routes(mcp_path="/mcp")`
  to the FastAPI router at the root, so clients find
  `/.well-known/oauth-protected-resource/mcp-server/mcp`.
- `auth.build_auth()` sets `resource_base_url` to `<HOLY_MOLY_PUBLIC_URL>/mcp-server`, so the metadata
  document and the `WWW-Authenticate` challenge name the real endpoint.
- `web_server.lifespan` enters the sub-app's lifespan (`mcp_app.lifespan`) before loading the models.
  Without it the endpoint accepts connections and fails on the first request with "task group was not
  initialized".
- `fastmcp` is pinned to `>=4.1,<5` in both packages; `http_app()` has changed between majors.

## Client configuration

```bash
claude mcp add --transport http holy-moly https://holy-moly.intern.chaintronics.com/mcp-server/mcp \
  --header "Authorization: Bearer ${HOLY_MOLY_TOKEN}"
```

```jsonc
// .vscode/mcp.json
"holy-moly-http": {
  "type": "http",
  "url": "https://holy-moly.intern.chaintronics.com/mcp-server/mcp",
  "headers": { "Authorization": "Bearer ${HOLY_MOLY_TOKEN}" }
}
```

Tokens come from the web UI ("API-Tokens" under the user name). Without a header, OAuth-capable
clients receive a 401 with `resource_metadata` and can log in through Keycloak.

## Verification

```bash
B=https://holy-moly.intern.chaintronics.com
H='-H Content-Type:application/json -H Accept:application/json,text/event-stream'

# 401 + resource_metadata without a token
curl -si -X POST $H -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' $B/mcp-server/mcp | head -3
# metadata at the root
curl -s $B/.well-known/oauth-protected-resource/mcp-server/mcp | jq .
# handshake + tool list with a token (session id from the initialize response headers)
curl -s -D /tmp/h -X POST $H -H "Authorization: Bearer $HOLY_MOLY_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' $B/mcp-server/mcp
SID=$(grep -i '^mcp-session-id:' /tmp/h | tr -d '\r' | awk '{print $2}')
curl -s -X POST $H -H "Authorization: Bearer $HOLY_MOLY_TOKEN" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' $B/mcp-server/mcp
# the REST API and the UI must be untouched by the mount
curl -s -o /dev/null -w '%{http_code}\n' $B/docs
```

Unit tests: `server/tests/test_mcp_http.py` (metadata route, 401 challenge, initialize, tools/list,
tools/call with a personal token, catch-all not shadowing the mount) and `test_mcp_server_auth.py`.

## Not done on purpose

- No standalone HTTP process: a second port and process would only make sense if the MCP endpoint
  had to scale or restart independently of the web UI.
- `remote/` keeps its stdio transport. A proxy that receives HTTP and forwards HTTP adds a hop and
  buys nothing; it stays the fallback for clients that cannot speak HTTP.
