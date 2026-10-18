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
        self.assertEqual(names, {"convert_speech_to_text", "convert_pdf_to_markdown", "convert_html_to_pdf",
                                 "list_tts_voices", "convert_text_to_speech"})

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
        self.assertEqual(len(await self._tool_names(server)), 5)


if __name__ == "__main__":
    unittest.main()


class McpToolHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_speech_requires_exactly_one_source(self) -> None:
        self.assertIn("exactly one", await mcp_server.convert_speech_to_text())
        self.assertIn("exactly one", await mcp_server.convert_speech_to_text(file_path="/a.wav", content_base64="AAAA"))

    async def test_pdf_rejects_bad_inline_payload(self) -> None:
        self.assertIn("exactly one", await mcp_server.convert_pdf_to_markdown())
        self.assertIn("not valid base64", await mcp_server.convert_pdf_to_markdown(content_base64="%%%"))
        self.assertIn("file not found", await mcp_server.convert_pdf_to_markdown(file_path="/does/not/exist.pdf"))

    def test_finish_wav_patches_sizes(self) -> None:
        import struct
        header = struct.pack("<4sI4s", b"RIFF", 0xFFFFFFFF, b"WAVE") + struct.pack(
            "<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16) + struct.pack("<4sI", b"data", 0xFFFFFFFF)
        wav = mcp_server._finish_wav([header, b"\x00" * 100])
        self.assertEqual(struct.unpack_from("<I", wav, 4)[0], len(wav) - 8)
        self.assertEqual(struct.unpack_from("<I", wav, 40)[0], 100)

    def test_tts_task_id_format_is_accepted_by_the_service(self) -> None:
        import uuid
        from holy_moly_server.services import tts_local
        tts_local.validate_task_id(str(uuid.uuid4()))      # the form mcp_server uses
        with self.assertRaises(ValueError):
            tts_local.validate_task_id(uuid.uuid4().hex)    # the form that broke the first deployment

    async def test_voice_list_and_html_validation(self) -> None:
        self.assertIn("de_thorsten", await mcp_server.list_tts_voices())
        self.assertIn("empty", await mcp_server.convert_html_to_pdf("   "))
