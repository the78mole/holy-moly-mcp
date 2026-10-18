"""Personal API tokens: store, verifier and REST endpoints."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from holy_moly_server import api_tokens, auth, local_accounts, token_store

PASSWORD = "correct horse battery staple"
HASH = PasswordHasher().hash(PASSWORD)


def _config(**overrides) -> auth.AuthConfig:
    base = dict(public_url=None, realm_url=None, audience="holy-moly-api", dcr=False, token_spec="",
                local_users_configured=True, local_jwt_secret="s" * 48, local_token_ttl=3600, disabled=False)
    base.update(overrides)
    return auth.AuthConfig(**base)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(local_accounts.router)
    app.include_router(api_tokens.router)

    @app.get("/user", dependencies=[Depends(auth.require_user)])
    async def user_route() -> dict:
        return {"ok": True}

    @app.get("/admin", dependencies=[Depends(auth.require_admin)])
    async def admin_route() -> dict:
        return {"ok": True}

    @app.get("/me")
    async def me(token: auth.AccessToken = Depends(auth.current_token)) -> dict:
        return {"subject": token.subject, "roles": auth.roles_of(token.claims), "iss": token.claims.get("iss")}

    return app


class TokenStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = token_store.TokenStore(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def test_create_lookup_revoke(self) -> None:
        plaintext, rec = self.store.create(name="laptop", owner_subject="local:a", owner_username="a", roles=["holy-moly-user"])
        self.assertTrue(plaintext.startswith("hm_"))
        self.assertEqual(rec.prefix, plaintext[:12])
        self.assertIsNotNone(self.store.lookup(plaintext))
        self.assertIsNone(self.store.lookup("hm_nope"))
        self.assertIsNone(self.store.lookup(plaintext[:-1]))

        verifier = token_store.PersonalTokenVerifier(self.store)
        access = await verifier.verify_token(plaintext)
        self.assertEqual(access.subject, "local:a")
        self.assertEqual(auth.roles_of(access.claims), ["holy-moly-user"])
        self.assertIsNotNone(self.store.get(rec.id).last_used_at)

        self.assertTrue(self.store.revoke(rec.id, owner_subject="local:a"))
        self.assertIsNone(self.store.lookup(plaintext))
        self.assertIsNone(await verifier.verify_token(plaintext))
        self.assertFalse(self.store.revoke(rec.id, owner_subject="local:a"))  # already revoked

    async def test_expired_token_is_rejected(self) -> None:
        plaintext, rec = self.store.create(name="short", owner_subject="s", owner_username="s", roles=["r"], expires_in_seconds=1)
        self.assertIsNotNone(self.store.lookup(plaintext))
        with patch("holy_moly_server.token_store.time.time", return_value=time.time() + 5):
            self.assertIsNone(self.store.lookup(plaintext))
            self.assertFalse(self.store.get(rec.id).active)

    def test_owner_scoping(self) -> None:
        _, a = self.store.create(name="a", owner_subject="A", owner_username="a", roles=["r"])
        self.store.create(name="b", owner_subject="B", owner_username="b", roles=["r"])
        self.assertEqual([t.name for t in self.store.list_for("A")], ["a"])
        self.assertEqual(len(self.store.list_all()), 2)
        self.assertFalse(self.store.revoke(a.id, owner_subject="B"))
        self.assertTrue(self.store.revoke(a.id))  # admin path

    def test_database_file_is_private(self) -> None:
        mode = os.stat(self.store.path).st_mode & 0o777
        self.assertEqual(mode, 0o600)


class TokenApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        token_store.set_store(token_store.TokenStore(Path(self.tmp.name) / "t.sqlite3"))
        env = {auth.ENV_LOCAL_USERS: f"admin:{HASH}:{auth.ROLE_ADMIN},{auth.ROLE_USER};user:{HASH}:{auth.ROLE_USER}", auth.ENV_LOCAL_USERS_FILE: ""}
        self._env = patch.dict(os.environ, env); self._env.start()
        local_accounts.reload_local_users()
        local_accounts._limiter = local_accounts._AttemptLimiter(local_accounts.MAX_ATTEMPTS, local_accounts.ATTEMPT_WINDOW_SECONDS)
        static = "hm_static_machine_token"
        auth.configure(_config(token_spec=f"{auth.hash_api_token(static)}:ci:{auth.ROLE_USER}"))
        self.static = static
        self.client = TestClient(_app())

    def tearDown(self) -> None:
        self._env.stop()
        local_accounts.reload_local_users()
        token_store.get_store().close()
        token_store.set_store(None)
        auth.configure(_config(local_users_configured=False, local_jwt_secret=None))
        self.tmp.cleanup()

    def _login(self, username: str) -> dict[str, str]:
        res = self.client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def test_create_list_use_revoke(self) -> None:
        h = self._login("user")
        res = self.client.post("/api/v1/auth/tokens", json={"name": "VS Code", "expires_in_days": 30}, headers=h)
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        self.assertTrue(body["token"].startswith("hm_"))
        self.assertEqual(body["roles"], [auth.ROLE_USER])
        self.assertIsNotNone(body["expires_at"])

        listed = self.client.get("/api/v1/auth/tokens", headers=h).json()
        self.assertEqual([t["name"] for t in listed], ["VS Code"])
        self.assertNotIn("token", listed[0])          # plaintext never listed
        self.assertEqual(listed[0]["prefix"], body["token"][:12])

        pat = {"Authorization": f"Bearer {body['token']}"}
        self.assertEqual(self.client.get("/user", headers=pat).status_code, 200)
        self.assertEqual(self.client.get("/admin", headers=pat).status_code, 403)
        me = self.client.get("/me", headers=pat).json()
        self.assertEqual(me["subject"], "local:user")
        self.assertEqual(me["iss"], token_store.TOKEN_ISSUER)

        # a PAT cannot mint or revoke tokens
        self.assertEqual(self.client.post("/api/v1/auth/tokens", json={"name": "x"}, headers=pat).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/auth/tokens/{body['id']}", headers=pat).status_code, 403)

        self.assertEqual(self.client.delete(f"/api/v1/auth/tokens/{body['id']}", headers=h).status_code, 204)
        self.assertEqual(self.client.get("/user", headers=pat).status_code, 401)
        self.assertFalse(self.client.get("/api/v1/auth/tokens", headers=h).json()[0]["active"])
        self.assertEqual(self.client.delete(f"/api/v1/auth/tokens/{body['id']}?purge=true", headers=h).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/tokens", headers=h).json(), [])

    def test_roles_are_capped_by_owner(self) -> None:
        h = self._login("user")
        res = self.client.post("/api/v1/auth/tokens", json={"name": "x", "roles": [auth.ROLE_ADMIN, auth.ROLE_USER]}, headers=h)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["roles"], [auth.ROLE_USER])
        res = self.client.post("/api/v1/auth/tokens", json={"name": "x", "roles": [auth.ROLE_ADMIN]}, headers=h)
        self.assertEqual(res.status_code, 400)

    def test_admin_sees_and_revokes_everything(self) -> None:
        user_h, admin_h = self._login("user"), self._login("admin")
        tid = self.client.post("/api/v1/auth/tokens", json={"name": "u"}, headers=user_h).json()["id"]
        self.client.post("/api/v1/auth/tokens", json={"name": "a"}, headers=admin_h)
        self.assertEqual(self.client.get("/api/v1/auth/tokens?all=true", headers=user_h).status_code, 403)
        names = sorted(t["name"] for t in self.client.get("/api/v1/auth/tokens?all=true", headers=admin_h).json())
        self.assertEqual(names, ["a", "u"])
        self.assertEqual(self.client.get("/api/v1/auth/tokens", headers=admin_h).json()[0]["name"], "a")
        self.assertEqual(self.client.delete(f"/api/v1/auth/tokens/{tid}", headers=admin_h).status_code, 204)

    def test_other_users_token_is_invisible(self) -> None:
        user_h, admin_h = self._login("user"), self._login("admin")
        tid = self.client.post("/api/v1/auth/tokens", json={"name": "a"}, headers=admin_h).json()["id"]
        self.assertEqual(self.client.delete(f"/api/v1/auth/tokens/{tid}", headers=user_h).status_code, 404)

    def test_static_token_cannot_manage_tokens(self) -> None:
        static = {"Authorization": f"Bearer {self.static}"}
        self.assertEqual(self.client.get("/user", headers=static).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/tokens", json={"name": "x"}, headers=static).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/auth/tokens", headers=static).json(), [])


if __name__ == "__main__":
    unittest.main()
