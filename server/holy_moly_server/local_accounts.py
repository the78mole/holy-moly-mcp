"""Local accounts: argon2 password hashes from env or file, exchanged for short-lived HS256 JWTs.

This is the fallback that works without Keycloak: for the first login after a deployment, for
small installations without an identity provider, and as an emergency entrance when Keycloak
is unreachable. Deliberately minimal: no self-registration, no password reset, no user UI.

Configuration (see ``docs/auth-oidc-and-tokens.md``, section 7)::

    HOLY_MOLY_LOCAL_USERS="admin:$argon2id$...:holy-moly-admin,holy-moly-user;gast:$argon2id$...:holy-moly-user"
    HOLY_MOLY_LOCAL_USERS_FILE=/run/secrets/holy-moly-local-users   # one entry per line, '#' comments
    HOLY_MOLY_LOCAL_JWT_SECRET=<random, >= 32 bytes>
    HOLY_MOLY_LOCAL_TOKEN_TTL=28800

Create a password hash with::

    python -m holy_moly_server.local_accounts hash
"""

from __future__ import annotations

import getpass
import os
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, status
from joserfc import jwk, jwt
from pydantic import BaseModel, Field

from holy_moly_server import auth as auth_module
from holy_moly_server.auth import ENV_LOCAL_USERS, ENV_LOCAL_USERS_FILE, LOCAL_ISSUER

_hasher = PasswordHasher()
# Verified against when the username is unknown, so the response time does not reveal
# whether an account exists.
_DUMMY_HASH = _hasher.hash(os.urandom(16).hex())

MAX_ATTEMPTS = 10
ATTEMPT_WINDOW_SECONDS = 15 * 60


@dataclass(frozen=True)
class LocalUser:
    name: str
    password_hash: str
    roles: tuple[str, ...]


def parse_local_users(text: str) -> dict[str, LocalUser]:
    """Parse ``name:<argon2-hash>:role[,role]`` entries separated by newlines or semicolons."""
    users: dict[str, LocalUser] = {}
    for raw_line in text.replace(";", "\n").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"local user entry {line[:20]!r}...: expected 'name:<argon2-hash>:role[,role]'")
        name, password_hash, roles = (p.strip() for p in parts)
        if not name:
            raise ValueError("local user entry without a name")
        if not password_hash.startswith("$argon2"):
            raise ValueError(
                f"local user {name!r}: password hash is not an argon2 hash "
                "(did Docker Compose swallow the '$' signs? write them as '$$' or use "
                f"{ENV_LOCAL_USERS_FILE})"
            )
        role_list = tuple(r.strip() for r in roles.split(",") if r.strip())
        if not role_list:
            raise ValueError(f"local user {name!r} has no roles")
        if name in users:
            raise ValueError(f"local user {name!r} is defined twice")
        users[name] = LocalUser(name=name, password_hash=password_hash, roles=role_list)
    return users


def load_local_users() -> dict[str, LocalUser]:
    """Read local accounts from ``HOLY_MOLY_LOCAL_USERS_FILE`` (preferred) or ``HOLY_MOLY_LOCAL_USERS``."""
    path = (os.environ.get(ENV_LOCAL_USERS_FILE) or "").strip()
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = os.environ.get(ENV_LOCAL_USERS) or ""
    return parse_local_users(text)


_users: dict[str, LocalUser] = {}


def reload_local_users() -> dict[str, LocalUser]:
    """(Re)load the account table from the environment; called at import and by tests."""
    global _users
    _users = load_local_users()
    return _users


def get_local_users() -> dict[str, LocalUser]:
    return _users


reload_local_users()


# ---------------------------------------------------------------------------
# Brute-force limiter (per client IP and per username, in-memory, single instance)
# ---------------------------------------------------------------------------


class _AttemptLimiter:
    def __init__(self, max_attempts: int, window_seconds: int):
        self._max = max_attempts
        self._window = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._attempts[key]
        while bucket and bucket[0] <= now - self._window:
            bucket.popleft()
        return bucket

    def is_blocked(self, key: str) -> bool:
        return len(self._prune(key, time.monotonic())) >= self._max

    def record_failure(self, key: str) -> None:
        self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


_limiter = _AttemptLimiter(MAX_ATTEMPTS, ATTEMPT_WINDOW_SECONDS)


# ---------------------------------------------------------------------------
# Token issuing
# ---------------------------------------------------------------------------


def issue_token(user: LocalUser) -> tuple[str, int]:
    """Sign a short-lived HS256 JWT for ``user``; ``auth.build_auth`` verifies it."""
    secret = auth_module.config.local_jwt_secret
    if not secret:
        raise RuntimeError("local accounts are not enabled (no local JWT secret)")
    ttl = auth_module.config.local_token_ttl
    now = int(time.time())
    claims = {
        "iss": LOCAL_ISSUER,
        "aud": auth_module.config.audience,
        "sub": f"local:{user.name}",
        "preferred_username": user.name,
        "realm_access": {"roles": list(user.roles)},  # same shape as Keycloak
        "iat": now,
        "exp": now + ttl,
    }
    token = jwt.encode({"alg": "HS256", "typ": "JWT"}, claims, jwk.OctKey.import_key(secret))
    return token, ttl


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


def _client_key(request: Request, username: str) -> tuple[str, str]:
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}", f"user:{username}"


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request) -> LoginResponse:
    """Exchange a local account's username and password for a short-lived bearer token."""
    users = get_local_users()
    if not users:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Local accounts are not enabled")

    ip_key, user_key = _client_key(request, payload.username)
    if _limiter.is_blocked(ip_key) or _limiter.is_blocked(user_key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts, try again later",
            headers={"Retry-After": str(ATTEMPT_WINDOW_SECONDS)},
        )

    user = users.get(payload.username)
    try:
        _hasher.verify(user.password_hash if user else _DUMMY_HASH, payload.password)
        valid = user is not None
    except (VerifyMismatchError, InvalidHashError):
        valid = False

    if not valid or user is None:
        _limiter.record_failure(ip_key)
        _limiter.record_failure(user_key)
        print(f"auth: failed local login for {payload.username!r} from {ip_key}", file=sys.stderr, flush=True)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    _limiter.reset(user_key)
    token, ttl = issue_token(user)
    print(f"auth: local login for {user.name!r} from {ip_key}", file=sys.stderr, flush=True)
    return LoginResponse(access_token=token, expires_in=ttl)


@router.get("/config")
async def auth_config() -> dict:
    """Public: tells the SPA which login methods exist. Contains no secrets."""
    cfg = auth_module.config
    return {
        "oidc": {"realm_url": cfg.realm_url, "client_id": "holy-moly-web"} if cfg.oidc_enabled else None,
        "local": bool(get_local_users()),
        "disabled": auth_module.auth is None and cfg.disabled,
    }


# ---------------------------------------------------------------------------
# CLI: python -m holy_moly_server.local_accounts hash
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] != ["hash"]:
        print("usage: python -m holy_moly_server.local_accounts hash", file=sys.stderr)
        return 2
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat: ")
    if first != second:
        print("passwords do not match", file=sys.stderr)
        return 1
    if len(first) < 12:
        print("password must be at least 12 characters", file=sys.stderr)
        return 1
    print(_hasher.hash(first))
    return 0


if __name__ == "__main__":
    sys.exit(main())
