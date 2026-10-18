#!/usr/bin/env python3
"""Create or update the Keycloak realm for holy-moly-web (docs/auth-oidc-and-tokens.md, section 4).

Idempotent: every object is created only if missing and otherwise updated in place.
Standard library only, so it runs anywhere with Python 3.10+.

Environment:
    KC_URL              Keycloak base URL, e.g. https://keycloak.example.com
    KC_ADMIN_USER       admin user in the master realm           (or KC_ADMIN_TOKEN / client credentials)
    KC_ADMIN_PASSWORD   its password
    KC_ADMIN_CLIENT_ID  alternatively an admin service account in the master realm
    KC_ADMIN_CLIENT_SECRET  its secret                           (kc.sh bootstrap-admin service)
    KC_ADMIN_TOKEN      alternatively a ready admin access token
    KC_TEMP_PASSWORD_FILE  with --create-user: write the temporary password to this file (mode 600) instead of stdout
    KC_REALM            realm to create/update            (default: holy-moly)
    HOLY_MOLY_PUBLIC_URL  public URL of holy-moly-web, used for redirect URIs and web origins
    HOLY_MOLY_DEV_ORIGIN  extra dev origin                 (default: http://localhost:5173)

Usage:
    python scripts/keycloak_setup.py                 # create/update realm, roles, clients, mapper
    python scripts/keycloak_setup.py --grant-admin daniel   # also give realm user 'daniel' the admin role
    python scripts/keycloak_setup.py --create-user daniel   # create the user if missing (temporary password printed)
    python scripts/keycloak_setup.py --verify               # end-to-end check with a temporary service-account client

--verify creates a confidential client `holy-moly-verify` (service account, realm role holy-moly-user,
same audience mapper as the SPA), fetches a client_credentials token, calls the backend at
HOLY_MOLY_PUBLIC_URL (GET /api/v1/model/info -> 200, GET /api/v1/system/info -> 403) and deletes the
client again. Only status codes and token claim *shapes* are printed; the client secret and the token
stay in memory.

Never pass secrets on the command line; read them from the environment (e.g. `bao kv get -field=...`).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

ROLE_USER = "holy-moly-user"
ROLE_ADMIN = "holy-moly-admin"
WEB_CLIENT = "holy-moly-web"
API_CLIENT = "holy-moly-api"
VERIFY_CLIENT = "holy-moly-verify"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        sys.exit(f"missing environment variable {name}")
    return value


class Keycloak:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, body: dict | list | None = None, *, raw: bool = False):
        url = f"{self.base}{path}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                payload = res.read()
                if raw:
                    return res
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            sys.exit(f"{method} {path} -> HTTP {exc.code}: {detail[:400]}")

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, body):
        return self._request("POST", path, body)

    def put(self, path: str, body):
        return self._request("PUT", path, body)

    def delete(self, path: str):
        return self._request("DELETE", path)


def admin_token(base_url: str) -> str:
    if os.environ.get("KC_ADMIN_TOKEN"):
        return os.environ["KC_ADMIN_TOKEN"]
    if os.environ.get("KC_ADMIN_CLIENT_ID"):
        grant = {
            "grant_type": "client_credentials",
            "client_id": env("KC_ADMIN_CLIENT_ID"),
            "client_secret": env("KC_ADMIN_CLIENT_SECRET"),
        }
    else:
        grant = {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": env("KC_ADMIN_USER"),
            "password": env("KC_ADMIN_PASSWORD"),
        }
    form = urllib.parse.urlencode(grant).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/realms/master/protocol/openid-connect/token", data=form, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)["access_token"]
    except urllib.error.HTTPError as exc:
        sys.exit(f"admin login failed: HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}")


def ensure_realm(kc: Keycloak, realm: str) -> None:
    existing = {r["realm"] for r in kc.get("/admin/realms")}
    desired = {
        "realm": realm,
        "enabled": True,
        "displayName": "Holy Moly",
        "accessTokenLifespan": 900,  # 15 min; the SPA refreshes silently
        "ssoSessionIdleTimeout": 8 * 3600,
        "ssoSessionMaxLifespan": 24 * 3600,
        "revokeRefreshToken": True,
        "refreshTokenMaxReuse": 0,
        "bruteForceProtected": True,
        "sslRequired": "external",
    }
    if realm in existing:
        kc.put(f"/admin/realms/{realm}", desired)
        print(f"realm {realm}: updated")
    else:
        kc.post("/admin/realms", desired)
        print(f"realm {realm}: created")


def ensure_roles(kc: Keycloak, realm: str) -> None:
    roles = {r["name"]: r for r in kc.get(f"/admin/realms/{realm}/roles")}
    for name, description in (
        (ROLE_USER, "May use every conversion endpoint and MCP tool"),
        (ROLE_ADMIN, "May switch the globally loaded STT/TTS models"),
    ):
        if name not in roles:
            kc.post(f"/admin/realms/{realm}/roles", {"name": name, "description": description})
            print(f"role {name}: created")
        else:
            print(f"role {name}: exists")

    # Every user of the realm gets holy-moly-user automatically via the realm's default role.
    realm_rep = kc.get(f"/admin/realms/{realm}")
    default_role_name = realm_rep["defaultRole"]["name"]
    composites = {r["name"] for r in kc.get(f"/admin/realms/{realm}/roles/{default_role_name}/composites")}
    if ROLE_USER not in composites:
        user_role = kc.get(f"/admin/realms/{realm}/roles/{ROLE_USER}")
        kc.post(f"/admin/realms/{realm}/roles/{default_role_name}/composites", [user_role])
        print(f"default role {default_role_name}: now includes {ROLE_USER}")
    else:
        print(f"default role {default_role_name}: already includes {ROLE_USER}")


def _client_by_id(kc: Keycloak, realm: str, client_id: str) -> dict | None:
    found = kc.get(f"/admin/realms/{realm}/clients?clientId={urllib.parse.quote(client_id)}")
    return found[0] if found else None


def ensure_client(kc: Keycloak, realm: str, rep: dict) -> dict:
    existing = _client_by_id(kc, realm, rep["clientId"])
    if existing is None:
        kc.post(f"/admin/realms/{realm}/clients", rep)
        existing = _client_by_id(kc, realm, rep["clientId"])
        print(f"client {rep['clientId']}: created")
    else:
        kc.put(f"/admin/realms/{realm}/clients/{existing['id']}", {**existing, **rep})
        print(f"client {rep['clientId']}: updated")
    return existing


def ensure_web_client(kc: Keycloak, realm: str, public_url: str, dev_origin: str) -> dict:
    origins = [public_url] + ([dev_origin] if dev_origin else [])
    rep = {
        "clientId": WEB_CLIENT,
        "name": "Holy Moly Web UI",
        "protocol": "openid-connect",
        "publicClient": True,  # no client secret: SPA with PKCE
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": [f"{o}/*" for o in origins],
        "webOrigins": origins,
        "rootUrl": public_url,
        "baseUrl": public_url,
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": "##".join(f"{o}/*" for o in origins),
        },
        "defaultClientScopes": ["openid", "profile", "email", "roles", "web-origins", "basic"],
    }
    client = ensure_client(kc, realm, rep)
    ensure_audience_mapper(kc, realm, client)
    return client


def ensure_audience_mapper(kc: Keycloak, realm: str, client: dict) -> None:
    # Audience mapper: without it the access token's `aud` never contains holy-moly-api and the
    # backend's JWTVerifier(audience="holy-moly-api") rejects every token.
    mappers = kc.get(f"/admin/realms/{realm}/clients/{client['id']}/protocol-mappers/models")
    mapper = {
        "name": "audience holy-moly-api",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {
            "included.client.audience": API_CLIENT,
            "id.token.claim": "false",
            "access.token.claim": "true",
            "introspection.token.claim": "true",
        },
    }
    existing = next((m for m in mappers if m["name"] == mapper["name"]), None)
    if existing is None:
        kc.post(f"/admin/realms/{realm}/clients/{client['id']}/protocol-mappers/models", mapper)
        print("audience mapper: created")
    else:
        kc.put(f"/admin/realms/{realm}/clients/{client['id']}/protocol-mappers/models/{existing['id']}", {**existing, **mapper})
        print("audience mapper: updated")


def ensure_api_client(kc: Keycloak, realm: str) -> dict:
    rep = {
        "clientId": API_CLIENT,
        "name": "Holy Moly API (audience only)",
        "protocol": "openid-connect",
        "publicClient": False,
        "bearerOnly": True,  # never logs anyone in; only the token audience
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False,
        "serviceAccountsEnabled": False,
    }
    return ensure_client(kc, realm, rep)


def create_user(kc: Keycloak, realm: str, username: str) -> None:
    found = kc.get(f"/admin/realms/{realm}/users?username={urllib.parse.quote(username)}&exact=true")
    if found:
        print(f"user {username}: exists")
        return
    temp_password = secrets.token_urlsafe(16)
    kc.post(
        f"/admin/realms/{realm}/users",
        {
            "username": username,
            "enabled": True,
            "credentials": [{"type": "password", "value": temp_password, "temporary": True}],
            "requiredActions": ["UPDATE_PASSWORD"],
        },
    )
    target = os.environ.get("KC_TEMP_PASSWORD_FILE")
    if target:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{username}: {temp_password}\n")
        print(f"user {username}: created; temporary password written to {target} (change at first login)")
    else:
        print(f"user {username}: created with a temporary password (must be changed at first login):")
        print(f"  {temp_password}")


def grant_admin(kc: Keycloak, realm: str, username: str) -> None:
    found = kc.get(f"/admin/realms/{realm}/users?username={urllib.parse.quote(username)}&exact=true")
    if not found:
        sys.exit(f"user {username!r} not found in realm {realm}; create it first (--create-user) or federate it")
    user_id = found[0]["id"]
    current = {r["name"] for r in kc.get(f"/admin/realms/{realm}/users/{user_id}/role-mappings/realm")}
    if ROLE_ADMIN in current:
        print(f"user {username}: already has {ROLE_ADMIN}")
        return
    role = kc.get(f"/admin/realms/{realm}/roles/{ROLE_ADMIN}")
    kc.post(f"/admin/realms/{realm}/users/{user_id}/role-mappings/realm", [role])
    print(f"user {username}: granted {ROLE_ADMIN}")


def _http_status(url: str, token: str) -> int:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _jwt_claims(token: str) -> dict:
    """Decode the payload without verifying it; used only to report the claim *shape*."""
    import base64

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def verify(kc: Keycloak, base_url: str, realm: str, public_url: str) -> bool:
    """End-to-end check with a temporary confidential client; always deletes the client afterwards.

    Returns True when the backend answered 200 for a user endpoint and 403 for an admin endpoint.
    """
    rep = {
        "clientId": VERIFY_CLIENT,
        "name": "Holy Moly verification (temporary)",
        "description": "Created by scripts/keycloak_setup.py --verify; safe to delete.",
        "protocol": "openid-connect",
        "publicClient": False,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False,
        "serviceAccountsEnabled": True,
    }
    client = ensure_client(kc, realm, rep)
    ok = False
    try:
        ensure_audience_mapper(kc, realm, client)
        sa_user = kc.get(f"/admin/realms/{realm}/clients/{client['id']}/service-account-user")
        current = {r["name"] for r in kc.get(f"/admin/realms/{realm}/users/{sa_user['id']}/role-mappings/realm")}
        if ROLE_USER not in current:
            role = kc.get(f"/admin/realms/{realm}/roles/{ROLE_USER}")
            kc.post(f"/admin/realms/{realm}/users/{sa_user['id']}/role-mappings/realm", [role])
            print(f"verify: service account granted {ROLE_USER}")
        client_secret = kc.get(f"/admin/realms/{realm}/clients/{client['id']}/client-secret")["value"]

        form = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": VERIFY_CLIENT,
                "client_secret": client_secret,
                "scope": "openid",  # the backend's JWTVerifier requires the openid scope
            }
        ).encode()
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token", data=form, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                access_token = json.load(res)["access_token"]
        except urllib.error.HTTPError as exc:
            print(f"verify: token request failed: HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}")
            return False

        claims = _jwt_claims(access_token)
        aud = claims.get("aud", [])
        aud = [aud] if isinstance(aud, str) else list(aud)
        roles = claims.get("realm_access", {}).get("roles", [])
        scope = claims.get("scope", "")
        print(f"verify: token iss={claims.get('iss')} aud={aud} scope={scope!r} realm_roles={roles}")
        if API_CLIENT not in aud:
            print(f"verify: WARNING aud lacks {API_CLIENT}; check the audience mapper, not the backend")
        if ROLE_USER not in roles:
            print(f"verify: WARNING realm_access.roles lacks {ROLE_USER}")
        if "openid" not in scope.split():
            print("verify: WARNING scope lacks openid; the backend requires it")

        api = public_url.rstrip("/")
        user_status = _http_status(f"{api}/api/v1/model/info", access_token)
        admin_status = _http_status(f"{api}/api/v1/system/info", access_token)
        print(f"verify: GET /api/v1/model/info  -> {user_status} (expected 200)")
        print(f"verify: GET /api/v1/system/info -> {admin_status} (expected 403)")
        ok = user_status == 200 and admin_status == 403
    finally:
        kc.delete(f"/admin/realms/{realm}/clients/{client['id']}")
        print(f"client {VERIFY_CLIENT}: deleted")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grant-admin", metavar="USERNAME", help="give this realm user the holy-moly-admin role")
    parser.add_argument("--create-user", metavar="USERNAME", help="create this realm user if missing")
    parser.add_argument(
        "--verify",
        action="store_true",
        help=f"end-to-end check: temporary client {VERIFY_CLIENT} with a service account, token, two backend calls",
    )
    args = parser.parse_args()

    base_url = env("KC_URL")
    realm = os.environ.get("KC_REALM", "holy-moly")
    public_url = env("HOLY_MOLY_PUBLIC_URL").rstrip("/")
    dev_origin = os.environ.get("HOLY_MOLY_DEV_ORIGIN", "http://localhost:5173").rstrip("/")

    kc = Keycloak(base_url, admin_token(base_url))
    ensure_realm(kc, realm)
    ensure_roles(kc, realm)
    ensure_api_client(kc, realm)
    ensure_web_client(kc, realm, public_url, dev_origin)
    if args.create_user:
        create_user(kc, realm, args.create_user)
    if args.grant_admin:
        grant_admin(kc, realm, args.grant_admin)
    verified = verify(kc, base_url, realm, public_url) if args.verify else None

    print()
    print("Backend configuration for holy-moly-web:")
    print(f"  HOLY_MOLY_OIDC_REALM_URL={base_url.rstrip('/')}/realms/{realm}")
    print(f"  HOLY_MOLY_OIDC_AUDIENCE={API_CLIENT}")
    print(f"  HOLY_MOLY_PUBLIC_URL={public_url}")
    print(f"Discovery: {base_url.rstrip('/')}/realms/{realm}/.well-known/openid-configuration")
    if verified is False:
        sys.exit("verify: FAILED")


if __name__ == "__main__":
    main()
