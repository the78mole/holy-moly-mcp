"""Tests for the shared auth module and the local-account login endpoint.

These tests import only ``holy_moly_server.auth`` and ``holy_moly_server.local_accounts``, so
they run without the heavy conversion dependencies.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from holy_moly_server import auth, local_accounts

PASSWORD = "correct horse battery staple"
HASHER = PasswordHasher()


def _config(**overrides) -> auth.AuthConfig:
    base = dict(
        public_url=None,
        realm_url=None,
        audience="holy-moly-api",
        dcr=False,
        token_spec="",
        local_users_configured=False,
        local_jwt_secret=None,
        local_token_ttl=3600,
        disabled=False,
    )
    base.update(overrides)
    return auth.AuthConfig(**base)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(local_accounts.router)

    @app.get("/user", dependencies=[Depends(auth.require_user)])
    async def user_route() -> dict:
        return {"ok": True}

    @app.get("/admin", dependencies=[Depends(auth.require_admin)])
    async def admin_route() -> dict:
        return {"ok": True}

    @app.get("/me")
    async def me(token: auth.AccessToken = Depends(auth.current_token)) -> dict:
        return {"subject": token.subject, "roles": auth.roles_of(token.claims)}

    return app


class HashedTokenVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_known_token_and_maps_roles(self) -> None:
        token = "hm_test_token"
        spec = f"{auth.hash_api_token(token)}:ci:{auth.ROLE_USER},{auth.ROLE_ADMIN}"
        verifier = auth.HashedTokenVerifier(spec)

        result = await verifier.verify_token(token)

        self.assertIsNotNone(result)
        self.assertEqual(result.subject, "token:ci")
        self.assertEqual(auth.roles_of(result.claims), [auth.ROLE_USER, auth.ROLE_ADMIN])

    async def test_rejects_unknown_token(self) -> None:
        verifier = auth.HashedTokenVerifier(f"{auth.hash_api_token('a')}:ci:{auth.ROLE_USER}")
        self.assertIsNone(await verifier.verify_token("b"))

    def test_rejects_malformed_spec(self) -> None:
        with self.assertRaises(ValueError):
            auth.HashedTokenVerifier("not-a-digest:ci:role")
        with self.assertRaises(ValueError):
            auth.HashedTokenVerifier(f"{auth.hash_api_token('a')}:ci:")

    def test_ignores_blank_and_comment_entries(self) -> None:
        verifier = auth.HashedTokenVerifier(f" ; # comment ; {auth.hash_api_token('a')}:ci:{auth.ROLE_USER};")
        self.assertEqual(len(verifier), 1)


class LocalUserParsingTests(unittest.TestCase):
    def test_parses_semicolons_newlines_and_comments(self) -> None:
        h = HASHER.hash(PASSWORD)
        users = local_accounts.parse_local_users(f"# comment\nadmin:{h}:{auth.ROLE_ADMIN},{auth.ROLE_USER};guest:{h}:{auth.ROLE_USER}\n")
        self.assertEqual(set(users), {"admin", "guest"})
        self.assertEqual(users["admin"].roles, (auth.ROLE_ADMIN, auth.ROLE_USER))

    def test_rejects_non_argon2_hash(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            local_accounts.parse_local_users("admin:plaintext:role")
        self.assertIn("argon2", str(ctx.exception))

    def test_rejects_duplicates_and_missing_roles(self) -> None:
        h = HASHER.hash(PASSWORD)
        with self.assertRaises(ValueError):
            local_accounts.parse_local_users(f"a:{h}:r;a:{h}:r")
        with self.assertRaises(ValueError):
            local_accounts.parse_local_users(f"a:{h}:")

    def test_loads_from_file_when_set(self) -> None:
        import tempfile

        h = HASHER.hash(PASSWORD)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(f"admin:{h}:{auth.ROLE_ADMIN}\n")
        try:
            with patch.dict(os.environ, {auth.ENV_LOCAL_USERS_FILE: fh.name, auth.ENV_LOCAL_USERS: "ignored"}):
                users = local_accounts.load_local_users()
        finally:
            os.unlink(fh.name)
        self.assertEqual(list(users), ["admin"])


class ConfigTests(unittest.TestCase):
    def test_build_auth_returns_none_without_sources(self) -> None:
        self.assertIsNone(auth.build_auth(_config()))

    def test_ensure_auth_configured_raises_without_sources(self) -> None:
        auth.configure(_config())
        with self.assertRaises(RuntimeError):
            auth.ensure_auth_configured()

    def test_ensure_auth_configured_allows_disabled_dev_mode(self) -> None:
        auth.configure(_config(disabled=True))
        auth.ensure_auth_configured()  # must not raise

    def test_realm_requires_public_url(self) -> None:
        with self.assertRaises(RuntimeError):
            auth.build_auth(_config(realm_url="https://kc.example/realms/x"))

    def test_realm_builds_remote_auth_provider(self) -> None:
        provider = auth.build_auth(
            _config(realm_url="https://kc.example/realms/x", public_url="https://hm.example")
        )
        self.assertIsNotNone(provider)
        routes = provider.get_well_known_routes(mcp_path="/mcp-server/mcp")
        self.assertTrue(any("oauth-protected-resource" in r.path for r in routes))


class LocalLoginFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hash = HASHER.hash(PASSWORD)
        env = {
            auth.ENV_LOCAL_USERS: f"admin:{self.hash}:{auth.ROLE_ADMIN},{auth.ROLE_USER};user:{self.hash}:{auth.ROLE_USER}",
            auth.ENV_LOCAL_USERS_FILE: "",
        }
        self._env = patch.dict(os.environ, env)
        self._env.start()
        local_accounts.reload_local_users()
        local_accounts._limiter = local_accounts._AttemptLimiter(local_accounts.MAX_ATTEMPTS, local_accounts.ATTEMPT_WINDOW_SECONDS)
        auth.configure(_config(local_users_configured=True, local_jwt_secret="s" * 48))
        self.client = TestClient(_app())

    def tearDown(self) -> None:
        self._env.stop()
        local_accounts.reload_local_users()
        auth.configure(_config())

    def _login(self, username: str, password: str = PASSWORD):
        return self.client.post("/api/v1/auth/login", json={"username": username, "password": password})

    def test_config_endpoint_is_public(self) -> None:
        res = self.client.get("/api/v1/auth/config")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"oidc": None, "local": True, "disabled": False})

    def test_missing_token_is_401_with_challenge(self) -> None:
        res = self.client.get("/user")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.headers["WWW-Authenticate"], "Bearer")

    def test_garbage_token_is_401(self) -> None:
        res = self.client.get("/user", headers={"Authorization": "Bearer nope"})
        self.assertEqual(res.status_code, 401)

    def test_login_then_access(self) -> None:
        res = self._login("admin")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["expires_in"], 3600)

        headers = {"Authorization": f"Bearer {body['access_token']}"}
        self.assertEqual(self.client.get("/user", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/admin", headers=headers).status_code, 200)
        me = self.client.get("/me", headers=headers).json()
        self.assertEqual(me["subject"], "local:admin")
        self.assertEqual(me["roles"], [auth.ROLE_ADMIN, auth.ROLE_USER])

    def test_user_role_cannot_reach_admin_route(self) -> None:
        token = self._login("user").json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/user", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/admin", headers=headers).status_code, 403)

    def test_wrong_password_and_unknown_user_are_401(self) -> None:
        self.assertEqual(self._login("admin", "wrong password").status_code, 401)
        self.assertEqual(self._login("nobody").status_code, 401)

    def test_token_signed_with_other_secret_is_rejected(self) -> None:
        token = self._login("admin").json()["access_token"]
        auth.configure(_config(local_users_configured=True, local_jwt_secret="x" * 48))
        res = self.client.get("/user", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 401)

    def test_static_token_and_local_login_coexist(self) -> None:
        static = "hm_static"
        auth.configure(
            _config(
                local_users_configured=True,
                local_jwt_secret="s" * 48,
                token_spec=f"{auth.hash_api_token(static)}:ci:{auth.ROLE_USER}",
            )
        )
        self.assertEqual(self.client.get("/user", headers={"Authorization": f"Bearer {static}"}).status_code, 200)
        self.assertEqual(self.client.get("/admin", headers={"Authorization": f"Bearer {static}"}).status_code, 403)
        local = self._login("admin").json()["access_token"]
        self.assertEqual(self.client.get("/admin", headers={"Authorization": f"Bearer {local}"}).status_code, 200)

    def test_brute_force_limiter_blocks_after_max_attempts(self) -> None:
        for _ in range(local_accounts.MAX_ATTEMPTS):
            self.assertEqual(self._login("admin", "wrong").status_code, 401)
        res = self._login("admin")  # even the right password is now blocked
        self.assertEqual(res.status_code, 429)
        self.assertIn("Retry-After", res.headers)


class DisabledModeTests(unittest.TestCase):
    def test_disabled_mode_grants_dev_admin(self) -> None:
        auth.configure(_config(disabled=True))
        try:
            client = TestClient(_app())
            self.assertEqual(client.get("/admin").status_code, 200)
            self.assertEqual(client.get("/me").json()["subject"], "dev")
        finally:
            auth.configure(_config())

    def test_login_without_local_users_is_404(self) -> None:
        auth.configure(_config(disabled=True))
        try:
            with patch.dict(os.environ, {auth.ENV_LOCAL_USERS: "", auth.ENV_LOCAL_USERS_FILE: ""}):
                local_accounts.reload_local_users()
            res = TestClient(_app()).post("/api/v1/auth/login", json={"username": "a", "password": "b"})
            self.assertEqual(res.status_code, 404)
        finally:
            auth.configure(_config())


if __name__ == "__main__":
    unittest.main()
