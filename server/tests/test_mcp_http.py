"""MCP over Streamable HTTP mounted in FastAPI: auth challenge, RFC 9728 metadata, tool calls."""

from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.server.auth import MultiAuth, RemoteAuthProvider, require_roles
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl

from holy_moly_server import auth, mcp_http, token_store

PUBLIC = "https://hm.example"
MCP_URL = f"{auth.MCP_MOUNT_PATH}{auth.MCP_PATH}"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _rpc(method: str, params: dict | None = None, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def _parse(res) -> dict:
    """Streamable HTTP answers either JSON or an SSE body with one data: line."""
    if res.headers.get("content-type", "").startswith("application/json"):
        return res.json()
    for line in res.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"no JSON-RPC payload in response: {res.text[:200]}")


class McpHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = token_store.TokenStore(Path(self.tmp.name) / "t.sqlite3")
        # Same composition as auth.build_auth(): an OIDC-style server plus token verifiers.
        resource_base = f"{PUBLIC}{auth.MCP_MOUNT_PATH}"
        server = RemoteAuthProvider(
            # required_scopes mirrors a Keycloak-style verifier; MultiAuth below must not inherit it,
            # or personal tokens (no OAuth scopes) get "insufficient_scope" from the MCP middleware.
            token_verifier=JWTVerifier(public_key="x" * 48, algorithm="HS256", issuer="https://kc.example/realms/r",
                                       required_scopes=["openid"]),
            authorization_servers=[AnyHttpUrl("https://kc.example/realms/r")],
            base_url=PUBLIC,
            resource_base_url=resource_base,
        )
        self.provider = MultiAuth(
            server=server,
            verifiers=[token_store.PersonalTokenVerifier(self.store)],
            base_url=PUBLIC,
            resource_base_url=resource_base,
            required_scopes=[],
        )
        mcp = FastMCP("t", auth=self.provider)

        @mcp.tool(auth=[require_roles(auth.ROLE_USER, extract=auth.roles_of)])
        def ping() -> str:
            """Reply pong."""
            return "pong"

        # Same wiring as web_server.lifespan: the parent app must enter the MCP sub-app's lifespan.
        holder: dict = {}

        @asynccontextmanager
        async def lifespan(application: FastAPI):
            async with holder["mcp"].lifespan(application):
                yield

        app = FastAPI(lifespan=lifespan)
        holder["mcp"] = self.mcp_app = mcp_http.mount_mcp(app, mcp)

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> dict:  # stands in for the SPA catch-all
            return {"spa": full_path}

        self.app = app
        self.pat, _ = self.store.create(name="t", owner_subject="local:u", owner_username="u", roles=[auth.ROLE_USER])

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_well_known_metadata_is_served_at_the_root(self) -> None:
        path = f"/.well-known/oauth-protected-resource{MCP_URL}"
        self.assertEqual(mcp_http.well_known_paths(FastMCP("x", auth=self.provider)), [path])
        with TestClient(self.app) as client:
            res = client.get(path)
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertEqual(body["resource"], f"{PUBLIC}{MCP_URL}")
            self.assertEqual(body["authorization_servers"], ["https://kc.example/realms/r"])

    def test_unauthenticated_call_gets_401_with_metadata_pointer(self) -> None:
        with TestClient(self.app) as client:
            res = client.post(MCP_URL, json=_rpc("initialize"), headers=HEADERS)
            self.assertEqual(res.status_code, 401)
            challenge = res.headers.get("www-authenticate", "")
            self.assertIn(f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource{MCP_URL}"', challenge)

    def test_personal_token_can_initialize_and_call_tools(self) -> None:
        with TestClient(self.app) as client:
            h = {**HEADERS, "Authorization": f"Bearer {self.pat}"}
            res = client.post(MCP_URL, headers=h, json=_rpc("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}))
            self.assertEqual(res.status_code, 200, res.text)
            self.assertIn("serverInfo", _parse(res)["result"])
            session = res.headers.get("mcp-session-id")
            if session:
                h["Mcp-Session-Id"] = session
            client.post(MCP_URL, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

            tools = _parse(client.post(MCP_URL, headers=h, json=_rpc("tools/list", id_=2)))["result"]["tools"]
            self.assertEqual([t["name"] for t in tools], ["ping"])

            call = _parse(client.post(MCP_URL, headers=h, json=_rpc("tools/call", {"name": "ping", "arguments": {}}, id_=3)))
            self.assertEqual(call["result"]["content"][0]["text"], "pong")

    def test_spa_catch_all_is_not_shadowing_the_mount(self) -> None:
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/some/page").json(), {"spa": "some/page"})
            res = client.get(MCP_URL, headers={"Accept": "text/event-stream"})
            self.assertNotEqual(res.headers.get("content-type", "").split(";")[0], "application/json")  # not the SPA stub
            self.assertIn(res.status_code, (401, 405, 406))


if __name__ == "__main__":
    unittest.main()
