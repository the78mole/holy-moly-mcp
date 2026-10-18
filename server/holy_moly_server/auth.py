"""Shared authentication for REST and MCP.

Three credential sources feed one ``MultiAuth`` object:

- Keycloak OIDC access tokens (RS256, validated via the realm's JWKS),
- static bearer tokens for machine clients (stored only as SHA-256 hashes),
- short-lived HS256 tokens issued by ``local_accounts.py`` for local username/password logins.

The same ``auth`` object is passed to ``FastMCP(auth=...)`` for the HTTP transport and used by
the FastAPI dependencies below (``require_user`` / ``require_admin``). Roles are always read
from ``realm_access.roles`` so Keycloak tokens, static tokens and local tokens look alike.

See ``docs/auth-oidc-and-tokens.md`` for the full design.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastmcp.server.auth import AccessToken, MultiAuth, RemoteAuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider
from pydantic import AnyHttpUrl

from holy_moly_server.token_store import PersonalTokenVerifier, get_store

ROLE_USER = "holy-moly-user"
ROLE_ADMIN = "holy-moly-admin"
LOCAL_ISSUER = "holy-moly-local"

#: Where the MCP Streamable HTTP app is mounted inside the FastAPI app, and its own path.
#: The public MCP endpoint is therefore <HOLY_MOLY_PUBLIC_URL>/mcp-server/mcp.
MCP_MOUNT_PATH = "/mcp-server"
MCP_PATH = "/mcp"
DEFAULT_AUDIENCE = "holy-moly-api"

ENV_PUBLIC_URL = "HOLY_MOLY_PUBLIC_URL"
ENV_REALM_URL = "HOLY_MOLY_OIDC_REALM_URL"
ENV_AUDIENCE = "HOLY_MOLY_OIDC_AUDIENCE"
ENV_DCR = "HOLY_MOLY_OIDC_DCR"
ENV_API_TOKENS = "HOLY_MOLY_API_TOKENS"
ENV_LOCAL_USERS = "HOLY_MOLY_LOCAL_USERS"
ENV_LOCAL_USERS_FILE = "HOLY_MOLY_LOCAL_USERS_FILE"
ENV_LOCAL_JWT_SECRET = "HOLY_MOLY_LOCAL_JWT_SECRET"
ENV_LOCAL_TOKEN_TTL = "HOLY_MOLY_LOCAL_TOKEN_TTL"
ENV_AUTH_DISABLED = "HOLY_MOLY_AUTH_DISABLED"

# Generated once per process when local accounts are configured without an explicit secret.
# Every restart then invalidates all local sessions, which is acceptable for development only.
_GENERATED_LOCAL_SECRET = secrets.token_urlsafe(48)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthConfig:
    """Everything ``build_auth`` needs, read from the environment once."""

    public_url: str | None
    realm_url: str | None
    audience: str
    dcr: bool
    token_spec: str
    local_users_configured: bool
    local_jwt_secret: str | None
    local_token_ttl: int
    disabled: bool

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.realm_url)

    @property
    def local_enabled(self) -> bool:
        return self.local_users_configured

    @property
    def any_source(self) -> bool:
        return self.oidc_enabled or bool(self.token_spec) or self.local_enabled


def read_config() -> AuthConfig:
    """Read the auth configuration from environment variables."""
    realm_url = (os.environ.get(ENV_REALM_URL) or "").strip().rstrip("/") or None
    public_url = (os.environ.get(ENV_PUBLIC_URL) or "").strip().rstrip("/") or None
    local_users_configured = bool(
        (os.environ.get(ENV_LOCAL_USERS) or "").strip() or (os.environ.get(ENV_LOCAL_USERS_FILE) or "").strip()
    )
    local_secret = (os.environ.get(ENV_LOCAL_JWT_SECRET) or "").strip() or None
    if local_users_configured and local_secret is None:
        print(
            f"WARNING: {ENV_LOCAL_USERS}/{ENV_LOCAL_USERS_FILE} is set but {ENV_LOCAL_JWT_SECRET} is not; "
            "using a random per-process secret, so every restart invalidates all local logins.",
            file=sys.stderr,
            flush=True,
        )
        local_secret = _GENERATED_LOCAL_SECRET
    return AuthConfig(
        public_url=public_url,
        realm_url=realm_url,
        audience=(os.environ.get(ENV_AUDIENCE) or DEFAULT_AUDIENCE).strip(),
        dcr=_env_flag(ENV_DCR),
        token_spec=(os.environ.get(ENV_API_TOKENS) or "").strip(),
        local_users_configured=local_users_configured,
        local_jwt_secret=local_secret if local_users_configured else None,
        local_token_ttl=int(os.environ.get(ENV_LOCAL_TOKEN_TTL) or "28800"),
        disabled=_env_flag(ENV_AUTH_DISABLED),
    )


# ---------------------------------------------------------------------------
# Token verifiers
# ---------------------------------------------------------------------------


def roles_of(claims: dict[str, Any]) -> list[str]:
    """Keycloak puts realm roles under ``realm_access.roles``; our own tokens mirror that."""
    realm_access = claims.get("realm_access") or {}
    roles = realm_access.get("roles") or []
    return [str(r) for r in roles]


def hash_api_token(token: str) -> str:
    """SHA-256 hex digest as stored in ``HOLY_MOLY_API_TOKENS``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class HashedTokenVerifier(TokenVerifier):
    """Static bearer tokens, stored only as SHA-256 hashes.

    Spec format (semicolon-separated entries)::

        <sha256hex>:<name>:<role>[,<role>...];<sha256hex>:<name>:<role>

    The plaintext token never touches the server configuration. Verification is a
    constant-time comparison of hex digests.
    """

    def __init__(self, spec: str):
        super().__init__()
        self._tokens: dict[str, tuple[str, list[str]]] = {}
        for raw_entry in spec.split(";"):
            entry = raw_entry.strip()
            if not entry or entry.startswith("#"):
                continue
            parts = entry.split(":", 2)
            if len(parts) != 3:
                raise ValueError(
                    f"{ENV_API_TOKENS}: entry {entry[:12]!r}... must be '<sha256hex>:<name>:<role[,role]>'"
                )
            digest, name, roles = parts
            digest = digest.strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"{ENV_API_TOKENS}: entry for {name!r} does not carry a SHA-256 hex digest")
            role_list = [r.strip() for r in roles.split(",") if r.strip()]
            if not role_list:
                raise ValueError(f"{ENV_API_TOKENS}: entry for {name!r} has no roles")
            self._tokens[digest] = (name.strip(), role_list)

    def __len__(self) -> int:
        return len(self._tokens)

    async def verify_token(self, token: str) -> AccessToken | None:
        digest = hash_api_token(token)
        for stored, (name, roles) in self._tokens.items():
            if hmac.compare_digest(stored, digest):
                return AccessToken(
                    token=token,
                    client_id=f"token:{name}",
                    scopes=[],
                    subject=f"token:{name}",
                    claims={
                        "iss": "holy-moly-token",
                        "sub": f"token:{name}",
                        "preferred_username": name,
                        "realm_access": {"roles": roles},
                    },
                )
        return None


def build_auth(config: AuthConfig) -> MultiAuth | None:
    """Compose the auth provider from the configured sources; ``None`` when nothing is configured."""
    if not config.any_source:
        return None

    # The protected resource is the mounted MCP endpoint, not the site root: RFC 9728 metadata
    # and the WWW-Authenticate challenge must both name <public_url>/mcp-server/mcp.
    resource_base = f"{config.public_url}{MCP_MOUNT_PATH}" if config.public_url else None

    server = None
    if config.realm_url:
        if not config.public_url:
            raise RuntimeError(f"{ENV_PUBLIC_URL} is required when {ENV_REALM_URL} is set")
        if config.dcr:
            server = KeycloakAuthProvider(
                realm_url=config.realm_url,
                base_url=config.public_url,
                audience=config.audience,
            )
            server.resource_base_url = AnyHttpUrl(resource_base)  # constructor has no parameter for it
        else:
            server = RemoteAuthProvider(
                token_verifier=JWTVerifier(
                    jwks_uri=f"{config.realm_url}/protocol/openid-connect/certs",
                    issuer=config.realm_url,
                    audience=config.audience,
                    algorithm="RS256",
                    required_scopes=["openid"],
                ),
                authorization_servers=[AnyHttpUrl(config.realm_url)],
                base_url=config.public_url,
                resource_base_url=resource_base,
                resource_name="holy-moly-web",
            )

    # Personal API tokens created in the web UI (token_store.py) come first: they are cheap to
    # check and carry a distinctive prefix.
    verifiers: list[TokenVerifier] = [PersonalTokenVerifier(get_store())]
    if config.local_enabled and config.local_jwt_secret:
        verifiers.append(
            JWTVerifier(
                public_key=config.local_jwt_secret,  # for HS* this parameter carries the shared secret
                algorithm="HS256",
                issuer=LOCAL_ISSUER,
                audience=config.audience,
            )
        )
    if config.token_spec:
        verifiers.append(HashedTokenVerifier(config.token_spec))

    return MultiAuth(server=server, verifiers=verifiers, base_url=config.public_url, resource_base_url=resource_base)


# ---------------------------------------------------------------------------
# Module state (rebuilt by ``configure()``; tests call it with a custom config)
# ---------------------------------------------------------------------------

config: AuthConfig = read_config()
auth: MultiAuth | None = None


def configure(new_config: AuthConfig | None = None) -> MultiAuth | None:
    """(Re)build the module-level ``config`` and ``auth`` objects."""
    global config, auth
    config = new_config or read_config()
    auth = build_auth(config)
    return auth


def ensure_auth_configured() -> None:
    """Refuse to start an unauthenticated service unless explicitly allowed for development."""
    if auth is None and not config.disabled:
        raise RuntimeError(
            "No authentication configured. Set at least one of "
            f"{ENV_REALM_URL}, {ENV_API_TOKENS}, {ENV_LOCAL_USERS}/{ENV_LOCAL_USERS_FILE}, "
            f"or set {ENV_AUTH_DISABLED}=true for local development only."
        )
    if auth is None:
        print(
            f"WARNING: {ENV_AUTH_DISABLED} is set; every request is treated as an admin. "
            "Never expose this instance beyond localhost.",
            file=sys.stderr,
            flush=True,
        )


configure()


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)

_DEV_TOKEN = AccessToken(
    token="",
    client_id="dev",
    scopes=[],
    subject="dev",
    claims={"sub": "dev", "preferred_username": "dev", "realm_access": {"roles": [ROLE_USER, ROLE_ADMIN]}},
)


def protected_resource_metadata_url() -> str | None:
    """RFC 9728 metadata URL served for the MCP endpoint (only when an OIDC realm is configured)."""
    if config.oidc_enabled and config.public_url:
        return f"{config.public_url}/.well-known/oauth-protected-resource{MCP_MOUNT_PATH}{MCP_PATH}"
    return None


def _www_authenticate() -> str:
    url = protected_resource_metadata_url()
    return f'Bearer resource_metadata="{url}"' if url else "Bearer"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": _www_authenticate()},
    )


async def current_token(
    request: Request,  # noqa: ARG001 - kept for future per-request logging
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AccessToken:
    """Resolve the bearer token of the current request into an ``AccessToken``."""
    if auth is None:
        if config.disabled:
            return _DEV_TOKEN
        raise _unauthorized("Authentication is not configured")
    if creds is None or not creds.credentials:
        raise _unauthorized("Bearer token required")
    token = await auth.verify_token(creds.credentials)
    if token is None:
        raise _unauthorized("Invalid or expired bearer token")
    return token


def require_role(role: str) -> Callable[..., Awaitable[AccessToken]]:
    """Dependency factory: the caller must carry ``role`` in ``realm_access.roles``."""

    async def dependency(token: AccessToken = Depends(current_token)) -> AccessToken:
        if role not in roles_of(token.claims):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Role '{role}' required")
        return token

    dependency.__name__ = f"require_{role.replace('-', '_')}"
    return dependency


require_user = require_role(ROLE_USER)
require_admin = require_role(ROLE_ADMIN)
