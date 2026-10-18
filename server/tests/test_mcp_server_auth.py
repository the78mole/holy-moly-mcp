"""The MCP server factory must attach role checks for HTTP only.

Without a bearer token (stdio, in-memory clients) FastMCP hides every tool that carries an
``auth`` check, so the stdio server must be built without them.
"""

from __future__ import annotations

import unittest

from fastmcp import Client

from holy_moly_server import auth, mcp_server


def _config(**overrides) -> auth.AuthConfig:
    base = dict(
        public_url=None, realm_url=None, audience="holy-moly-api", dcr=False, token_spec="",
        local_users_configured=False, local_jwt_secret=None, local_token_ttl=3600, disabled=False,
    )
    base.update(overrides)
    return auth.AuthConfig(**base)


class McpServerAuthTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        auth.configure(_config())

    async def _tool_names(self, server) -> set[str]:
        async with Client(server) as client:
            return {t.name for t in await client.list_tools()}

    async def test_stdio_server_exposes_tools_without_auth(self) -> None:
        auth.configure(_config(token_spec=f"{auth.hash_api_token('x')}:ci:{auth.ROLE_USER}"))
        names = await self._tool_names(mcp_server.create_server(http=False))
        self.assertEqual(names, {"convert_speech_to_text", "convert_pdf_to_markdown"})

    async def test_http_server_hides_tools_from_unauthenticated_clients(self) -> None:
        auth.configure(_config(token_spec=f"{auth.hash_api_token('x')}:ci:{auth.ROLE_USER}"))
        server = mcp_server.create_server(http=True)
        self.assertIs(server.auth, auth.auth)
        self.assertEqual(await self._tool_names(server), set())

    async def test_http_server_without_auth_config_exposes_tools(self) -> None:
        # HOLY_MOLY_AUTH_DISABLED development mode: no provider, no checks.
        auth.configure(_config(disabled=True))
        server = mcp_server.create_server(http=True)
        self.assertIsNone(server.auth)
        self.assertEqual(len(await self._tool_names(server)), 2)


if __name__ == "__main__":
    unittest.main()
