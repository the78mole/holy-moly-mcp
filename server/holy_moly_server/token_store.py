"""Personal API tokens: created by users in the web UI, stored as SHA-256 hashes in SQLite.

The database lives in ``HOLY_MOLY_DATA_DIR`` (default ``/app/data`` in the container, a named
volume in docker-compose), so tokens survive image rebuilds and container re-creation.

A token inherits (a subset of) its owner's roles at creation time. ``PersonalTokenVerifier`` plugs
into the shared ``MultiAuth`` (see ``auth.py``) and turns a presented token into an ``AccessToken``
whose ``realm_access.roles`` carry the stored roles, so REST dependencies and MCP tool checks work
unchanged. Only hashes are stored; the plaintext is shown exactly once at creation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastmcp.server.auth import AccessToken, TokenVerifier

ENV_DATA_DIR = "HOLY_MOLY_DATA_DIR"
TOKEN_PREFIX = "hm_"
TOKEN_RANDOM_BYTES = 32
DISPLAY_PREFIX_LEN = 12  # "hm_" + 9 characters, enough to recognise a token in a client config
LAST_USED_WRITE_INTERVAL = 60  # seconds; avoid a write on every request
TOKEN_ISSUER = "holy-moly-pat"


def data_dir() -> Path:
    configured = (os.environ.get(ENV_DATA_DIR) or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "data"


def default_db_path() -> Path:
    return data_dir() / "tokens.sqlite3"


@dataclass(frozen=True)
class ApiToken:
    id: int
    name: str
    prefix: str
    owner_subject: str
    owner_username: str
    roles: tuple[str, ...]
    created_at: int
    expires_at: int | None
    last_used_at: int | None
    revoked_at: int | None

    @property
    def active(self) -> bool:
        now = int(time.time())
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "prefix": self.prefix,
            "owner": self.owner_username,
            "roles": list(self.roles),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
            "active": self.active,
        }


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    """SQLite-backed store. Safe for use from several threads; the GIL plus a lock serialise writes."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash     TEXT NOT NULL UNIQUE,
                prefix         TEXT NOT NULL,
                name           TEXT NOT NULL,
                owner_subject  TEXT NOT NULL,
                owner_username TEXT NOT NULL,
                roles          TEXT NOT NULL,
                created_at     INTEGER NOT NULL,
                expires_at     INTEGER,
                last_used_at   INTEGER,
                revoked_at     INTEGER
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_owner ON api_tokens(owner_subject)")

    @staticmethod
    def _row(row: sqlite3.Row) -> ApiToken:
        return ApiToken(
            id=row["id"],
            name=row["name"],
            prefix=row["prefix"],
            owner_subject=row["owner_subject"],
            owner_username=row["owner_username"],
            roles=tuple(json.loads(row["roles"])),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            revoked_at=row["revoked_at"],
        )

    # -- writes ---------------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        owner_subject: str,
        owner_username: str,
        roles: list[str],
        expires_in_seconds: int | None = None,
    ) -> tuple[str, ApiToken]:
        """Create a token. Returns (plaintext, record). The plaintext is never stored."""
        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_RANDOM_BYTES)
        now = int(time.time())
        expires_at = now + expires_in_seconds if expires_in_seconds else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO api_tokens (token_hash, prefix, name, owner_subject, owner_username, roles, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hash_token(plaintext),
                    plaintext[:DISPLAY_PREFIX_LEN],
                    name.strip()[:100] or "token",
                    owner_subject,
                    owner_username,
                    json.dumps(sorted(set(roles))),
                    now,
                    expires_at,
                ),
            )
            token_id = cur.lastrowid
        record = self.get(token_id)
        assert record is not None
        return plaintext, record

    def revoke(self, token_id: int, *, owner_subject: str | None = None) -> bool:
        """Revoke a token; with ``owner_subject`` only when it belongs to that owner. Returns success."""
        with self._lock:
            if owner_subject is None:
                cur = self._conn.execute(
                    "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (int(time.time()), token_id)
                )
            else:
                cur = self._conn.execute(
                    "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND owner_subject = ? AND revoked_at IS NULL",
                    (int(time.time()), token_id, owner_subject),
                )
            return cur.rowcount > 0

    def delete(self, token_id: int, *, owner_subject: str | None = None) -> bool:
        """Remove a token row entirely (used for revoked tokens the owner wants gone from the list)."""
        with self._lock:
            if owner_subject is None:
                cur = self._conn.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
            else:
                cur = self._conn.execute("DELETE FROM api_tokens WHERE id = ? AND owner_subject = ?", (token_id, owner_subject))
            return cur.rowcount > 0

    def touch(self, token_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (int(time.time()), token_id))

    # -- reads ----------------------------------------------------------------------------

    def get(self, token_id: int) -> ApiToken | None:
        row = self._conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
        return self._row(row) if row else None

    def list_for(self, owner_subject: str) -> list[ApiToken]:
        rows = self._conn.execute(
            "SELECT * FROM api_tokens WHERE owner_subject = ? ORDER BY created_at DESC", (owner_subject,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def list_all(self) -> list[ApiToken]:
        rows = self._conn.execute("SELECT * FROM api_tokens ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def lookup(self, plaintext: str) -> ApiToken | None:
        """Find the active token record for a presented plaintext token, or None."""
        if not plaintext.startswith(TOKEN_PREFIX):
            return None
        row = self._conn.execute("SELECT * FROM api_tokens WHERE token_hash = ?", (hash_token(plaintext),)).fetchone()
        if row is None:
            return None
        record = self._row(row)
        return record if record.active else None

    def close(self) -> None:
        self._conn.close()


class PersonalTokenVerifier(TokenVerifier):
    """fastmcp TokenVerifier backed by the TokenStore."""

    def __init__(self, store: TokenStore):
        super().__init__()
        self.store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        record = self.store.lookup(token)
        if record is None:
            return None
        now = int(time.time())
        if record.last_used_at is None or now - record.last_used_at >= LAST_USED_WRITE_INTERVAL:
            self.store.touch(record.id)
        return AccessToken(
            token=token,
            client_id=f"pat:{record.id}",
            scopes=[],
            subject=record.owner_subject,
            expires_at=record.expires_at,
            claims={
                "iss": TOKEN_ISSUER,
                "sub": record.owner_subject,
                "preferred_username": record.owner_username,
                "token_id": record.id,
                "token_name": record.name,
                "realm_access": {"roles": list(record.roles)},
            },
        )


_store: TokenStore | None = None


def get_store() -> TokenStore:
    """Process-wide store, opened on first use at the configured data dir."""
    global _store
    if _store is None:
        _store = TokenStore()
    return _store


def set_store(store: TokenStore | None) -> None:
    """Replace the process-wide store (tests, or a custom path)."""
    global _store
    _store = store
