"""REST API for personal API tokens (``/api/v1/auth/tokens``).

Every authenticated user can create, list and revoke their own tokens; ``holy-moly-admin`` may
list and revoke everyone's. Tokens are created only from an interactive session (Keycloak or
local login), never with another API token or a static ``HOLY_MOLY_API_TOKENS`` entry, so a
leaked machine token cannot mint further credentials.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from holy_moly_server.auth import LOCAL_ISSUER, ROLE_ADMIN, AccessToken, require_user, roles_of
from holy_moly_server.token_store import TOKEN_ISSUER, get_store

MAX_TOKENS_PER_USER = 50
MAX_EXPIRY_DAYS = 3650

router = APIRouter(prefix="/api/v1/auth/tokens", tags=["Auth"])


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Label shown in the token list, e.g. 'VS Code Laptop'.")
    expires_in_days: int | None = Field(None, ge=1, le=MAX_EXPIRY_DAYS, description="Lifetime in days; omit for no expiry.")
    roles: list[str] | None = Field(None, description="Subset of the caller's roles to grant; default: all of them.")


def _interactive_session_required(token: AccessToken) -> None:
    issuer = token.claims.get("iss")
    if issuer == TOKEN_ISSUER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="API tokens cannot create or manage API tokens; log in via the web UI.")
    if issuer not in (LOCAL_ISSUER, None) and not str(issuer).startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="A static API token cannot manage personal API tokens.")
    if issuer is None and token.client_id.startswith("token:"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="A static API token cannot manage personal API tokens.")


def _username(token: AccessToken) -> str:
    return str(token.claims.get("preferred_username") or token.subject or "unknown")


@router.get("")
async def list_tokens(
    all_users: bool = Query(False, alias="all", description="Admins only: list every user's tokens."),
    token: AccessToken = Depends(require_user),
) -> list[dict]:
    store = get_store()
    if all_users:
        if ROLE_ADMIN not in roles_of(token.claims):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Role '{ROLE_ADMIN}' required")
        return [t.as_dict() for t in store.list_all()]
    return [t.as_dict() for t in store.list_for(token.subject or "")]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_token(payload: CreateTokenRequest, token: AccessToken = Depends(require_user)) -> dict:
    """Create a personal API token. The plaintext is returned once and never stored."""
    _interactive_session_required(token)
    owner_roles = roles_of(token.claims)
    roles = owner_roles if payload.roles is None else [r for r in payload.roles if r in owner_roles]
    if not roles:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The token would have no roles.")
    store = get_store()
    subject = token.subject or ""
    if sum(1 for t in store.list_for(subject) if t.active) >= MAX_TOKENS_PER_USER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"At most {MAX_TOKENS_PER_USER} active tokens per user.")
    plaintext, record = store.create(
        name=payload.name,
        owner_subject=subject,
        owner_username=_username(token),
        roles=roles,
        expires_in_seconds=payload.expires_in_days * 86400 if payload.expires_in_days else None,
    )
    return {"token": plaintext, **record.as_dict()}


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: int,
    purge: bool = Query(False, description="Also remove the row from the list (only meaningful for revoked tokens)."),
    token: AccessToken = Depends(require_user),
) -> Response:
    """Revoke (and optionally delete) a token. Owners may touch their own, admins any."""
    _interactive_session_required(token)
    store = get_store()
    record = store.get(token_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Token not found")
    is_admin = ROLE_ADMIN in roles_of(token.claims)
    if record.owner_subject != (token.subject or "") and not is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Token not found")
    if purge:
        store.delete(token_id)
    else:
        store.revoke(token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
