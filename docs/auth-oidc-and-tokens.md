# Authentifizierung: OIDC (Keycloak) und Token-Auth

Konzept und Umsetzungsleitfaden, um `holy-moly-web` (REST-API, Vue-UI und den geplanten
MCP-HTTP-Endpunkt) mit zwei Zugangsarten abzusichern:

- **OIDC gegen Keycloak** für interaktive Nutzer (Web-UI) und MCP-Clients, die den
  OAuth-Discovery-Flow beherrschen (Claude Code, VS Code).
- **Statische Bearer-Tokens** für Maschinen-Clients ohne Browser (CLI-Skripte, cron,
  der `remote/`-Proxy, CI).
- **Lokale Konten** (Benutzername + Passwort aus Env oder Datei) als Fallback, wenn kein
  Keycloak erreichbar oder konfiguriert ist, und als Erstzugang nach dem Deployment.

Alle drei Wege münden in dieselbe Verifikations- und Autorisierungslogik, damit jeder Endpunkt
genau einmal geschützt wird. Dieses Dokument baut auf [http-transport.md](http-transport.md)
auf; der dort beschriebene Mount von `mcp.http_app()` in die FastAPI-App wird hier vorausgesetzt.

Alle Angaben zur `fastmcp`-API in diesem Dokument wurden am 2026-10-17 gegen `fastmcp==4.1.0`
(`mcp==2.3.0`) durch Introspektion der installierten Pakete verifiziert. Keycloak-Angaben
stammen aus der Keycloak-Dokumentation und sind beim Einrichten des Realms gegenzuprüfen.

## Stand der Umsetzung (2026-10-18)

| Phase | Status | Was liegt vor |
| --- | --- | --- |
| 0 | erledigt | `fastmcp>=4.1,<5` in beiden Paketen gepinnt, `argon2-cffi` ergänzt, `uv.lock` aktualisiert (fastmcp 4.1.0, mcp 2.3.0) |
| 1 | erledigt | `server/holy_moly_server/auth.py` (Config, `HashedTokenVerifier`, `build_auth`, FastAPI-Dependencies, Startabbruch), `local_accounts.py` (Login, `/auth/config`, Limiter, Hash-CLI), alle REST-Routen abgesichert, 24 Tests in `server/tests/test_auth.py` |
| 2 | erledigt | `RemoteAuthProvider`/`KeycloakAuthProvider` werden aus `HOLY_MOLY_OIDC_*` gebaut; Realm `holy-moly` auf keycloak.bizzmark.io per `scripts/keycloak_setup.py` angelegt (2026-10-18), Nutzer `daniel` mit `holy-moly-admin`; `--verify` gegen die Live-Instanz: 200/403 |
| 3 | offen | Frontend: Login-Ansicht, `apiFetch`, SSE auf `fetch` |
| 4 | vorbereitet | `mcp_server.create_server(http=True)` liefert den Server mit Auth-Provider und Tool-Checks; der Mount in FastAPI (http-transport.md) fehlt noch. 3 Tests in `server/tests/test_mcp_server_auth.py` |
| 5 | erledigt | `remote/`-Proxy sendet `HOLY_MOLY_API_TOKEN` nur an die API; `.vscode/mcp.json` hat den Passwort-Input; READMEs ergänzt |
| 6 | teilweise | Nginx Proxy Manager + TLS für holy-moly.intern.chaintronics.com stehen; Keycloak-Admin-Zugang in OpenBao (`it-infra`, `secret/keycloak-master-admin`); offen: OpenBao-Einträge für API-Token/lokales Konto, CORS-Dev-Origins |

Abweichung vom Entwurf unten: die Tool-Checks hängen nicht als Dekorator an den Tools, sondern
werden in `create_server(http=True)` gesetzt. Grund: FastMCP blendet Tools mit `auth`-Check
aus, sobald kein Bearer-Token im Kontext ist, und das trifft den stdio-Server und In-Memory-Clients.
Die Tool-Funktionen sind deshalb einfache Funktionen, die die Factory registriert.

## 1. Ausgangslage

holy-moly-mcp hat heute keinerlei Authentifizierung. Das funktioniert nur, weil der Dienst auf
einer internen Adresse (`172.22.7.224:8087`) lauscht. Mit dem HTTP-Transport für MCP wird der
Endpunkt für externe Clients direkt erreichbar, und Auth wird zur Voraussetzung.

| Komponente | Datei | Auth-Stand heute |
| --- | --- | --- |
| FastAPI-Backend (REST unter `/api/v1/*`) | `server/holy_moly_server/web_server.py` | keine; CORS nur für `localhost:5173` |
| Vue-3-SPA (gleicher Origin, `fetch` + `EventSource`) | `frontend/src/App.vue` | keine; SSE-Streams tragen keine Header |
| MCP-Server (FastMCP, stdio) | `server/holy_moly_server/mcp_server.py` | keine; stdio läuft lokal unter dem Nutzer |
| Remote-Proxy (stdio → HTTP) | `remote/holy_moly_remote/mcp_remote_server.py` | keine; nackte `httpx`-Requests an `HOLY_MOLY_API_URL` |
| MCP-HTTP-Endpunkt `/mcp-server/mcp` | geplant (http-transport.md) | offen; hier setzt dieses Dokument an |

Randbedingungen aus dem Repo:

- `fastmcp` ist in beiden Paketen **unpinned**. `uv.lock` steht auf 3.4.4, verifiziert wurde
  gegen 4.1.0. Die Auth-API (`MultiAuth`, `KeycloakAuthProvider`, `auth=` an `@mcp.tool`) gibt
  es in dieser Form erst in 4.x. Zuerst pinnen: `"fastmcp>=4.1,<5"`.
- `convert_html_to_pdf` und `convert_text_to_speech` (im Proxy) schreiben auf Pfade, die der
  Aufrufer wählt. Ohne Auth ist das ein Schreibzugriff für jeden im Netz.
- Die Modellwechsel-Endpunkte `/api/v1/model/load` und `/api/v1/tts/model/load` wirken global
  auf alle Nutzer, weil es nur eine geladene Modellinstanz gibt. Sie bekommen eine Admin-Rolle.

## 2. Ziele und Anforderungen

| Anforderung | Warum | Betrifft |
| --- | --- | --- |
| Alles außer `/`, SPA-Assets, `/docs`, `/openapi.json` und `/.well-known/*` verlangt ein gültiges Credential | Rechenlast und Dateischreibzugriff sonst offen | REST, MCP-HTTP |
| OIDC-Login per Authorization Code + PKCE, kein Client-Secret im Browser | Eine SPA kann kein Geheimnis halten | Vue-Frontend |
| Keycloak-Tokens werden lokal per JWKS validiert, kein Introspection-Call pro Request | Latenz; ein Keycloak-Ausfall darf laufende Konvertierungen nicht blockieren | Backend |
| Statische Tokens für Clients ohne Browser | OAuth-Flows sind in cron/CI/Proxy nicht praktikabel | MCP-HTTP, Proxy, REST |
| Mindestens ein lokales Konto (Name, Passwort-Hash, Rollen) ist in Compose/Env oder einer Datei konfigurierbar und funktioniert ohne Keycloak | Erstzugang, Keycloak-Ausfall, kleine Installationen ohne IdP | Backend, Vue-Frontend |
| Rollen: `user` darf konvertieren, `admin` darf Modelle wechseln | Modellwechsel wirkt global | REST `*/model/load` |
| Secrets nicht im Repo, nicht in `mcp.json` | OpenBao serverseitig, VS-Code-`inputs` clientseitig (wie heute beim API-URL-Input) | Deployment, Client-Config |
| MCP-Clients bekommen bei 401 Protected-Resource-Metadata nach RFC 9728 | Claude Code und VS Code starten damit den OAuth-Flow automatisch | MCP-HTTP |
| stdio-Server bleiben ohne Auth | Lokal, kein Netzwerk | `holy-moly-mcp` |

Nicht Ziel: Mandantentrennung, Quotas, Audit-Log. Die Auth-Schicht liefert dafür später die
Identität (`sub`, `preferred_username`), mehr nicht.

## 3. Architektur

```text
                         ┌──────────────────────────────────────────────────┐
                         │ Keycloak  https://kc.example/realms/holy-moly    │
                         │  - OIDC Discovery  /.well-known/openid-configuration
                         │  - JWKS            /protocol/openid-connect/certs │
                         │  - Clients: holy-moly-web (public, PKCE)         │
                         │             holy-moly-api (bearer-only, audience)│
                         └───────────▲──────────────────────────▲───────────┘
          Login (PKCE)               │ JWKS (gecacht)           │ Login/DCR
   ┌──────────────┐                  │                          │
   │ Vue-SPA      │ Bearer <JWT> ─┐  │                 ┌────────┴─────────┐
   └──────────────┘               │  │                 │ Claude Code /    │
                                  │  │                 │ VS Code (HTTP)   │
   ┌──────────────┐               ▼  │                 └────────┬─────────┘
   │ cron / CI /  │ Bearer <hm_…> ┌──┴───────────────────────┐  │ Bearer <JWT>
   │ remote-Proxy │──────────────▶│ holy-moly-web            │◀─┘
   └──────────────┘               │                          │
   ┌──────────────┐  POST /auth/  │  auth.py  ── MultiAuth   │
   │ Vue-SPA      │  login ──────▶│     ├─ KeycloakAuthProvider (JWT via JWKS)
   │ Login-Form   │◀── lokales    │     ├─ HashedTokenVerifier (statische Tokens)
   │ (Fallback)   │    JWT (HS256)│     └─ JWTVerifier HS256  (lokal ausgestellte JWTs)
   └──────────────┘               │  local_accounts.py ── Passwort-Hashes aus Env/Datei
                                  │                          │
                                  │  FastAPI  /api/v1/*   ──▶ Depends(require_user / require_admin)
                                  │  FastMCP  /mcp-server/mcp ─▶ auth=… an @mcp.tool
                                  │  SPA      /            (öffentlich)
                                  │  RFC 9728 /.well-known/oauth-protected-resource/… (öffentlich)
                                  └──────────────────────────┘
```

Kernentscheidung: **ein** Auth-Objekt (`fastmcp.server.auth.MultiAuth`) wird sowohl an
`FastMCP(auth=…)` übergeben als auch von einer FastAPI-Dependency benutzt. `MultiAuth` probiert
den Keycloak-Verifier zuerst, dann den Token-Verifier, dann den Verifier für lokal ausgestellte
JWTs, und liefert ein `AccessToken` mit `subject`, `scopes`, `claims`. Darauf bauen REST und MCP
ihre Rollenprüfung auf.

Lokale Konten erzeugen **kein** eigenes Auth-Verfahren pro Request: ein Login mit Name und
Passwort tauscht die Credentials einmal gegen ein kurzlebiges, vom Server selbst signiertes JWT
(HS256). Danach ist ein lokaler Nutzer für Backend und MCP nicht von einem Keycloak-Nutzer zu
unterscheiden; nur `iss` verrät die Quelle. Details in Abschnitt 7.

Warum nicht Keycloak-Introspection oder einen Auth-Proxy (oauth2-proxy) vor dem Container?

- Introspection kostet einen Roundtrip pro Request und koppelt die Verfügbarkeit an Keycloak.
  JWKS wird von `JWTVerifier` gecacht (`jwks_refresh_interval`, Default 30 s).
- Ein Auth-Proxy davor löst das Browser-Login, aber nicht die MCP-Discovery (RFC 9728) und nicht
  die Token-Auth für Maschinen. Beides muss die App selbst können.

## 4. Keycloak-Setup

Realm `holy-moly` (oder ein bestehender Realm), zwei Clients, zwei Realm-Rollen.

### 4.1 Client `holy-moly-web` (SPA)

| Einstellung | Wert | Grund |
| --- | --- | --- |
| Client type | OpenID Connect | |
| Client authentication | **Off** (public client) | SPA hält kein Secret |
| Standard flow | On | Authorization Code |
| Direct access grants, Implicit flow | Off | veraltet/unsicher |
| PKCE Code Challenge Method (Advanced) | `S256` | PKCE erzwingen |
| Valid redirect URIs | `https://holy-moly.example/*`, dev: `http://localhost:5173/*` | |
| Valid post logout redirect URIs | wie oben | |
| Web origins | `https://holy-moly.example`, `http://localhost:5173` | CORS für Token-Endpoint |
| Access Token Lifespan | 5–15 min | kurz, Refresh übernimmt |

### 4.2 Client `holy-moly-api` (Resource Server)

Dient nur als **Audience** und Träger der Client-Rollen. `Client authentication` kann On sein,
der Client braucht keinen Flow (alles Off). Wichtig: Keycloak schreibt den API-Client
standardmäßig **nicht** in `aud` des Access-Tokens. Dafür braucht der SPA-Client einen Mapper:

- Client `holy-moly-web` → Client scopes → `holy-moly-web-dedicated` → Add mapper → By
  configuration → **Audience** → Included Client Audience: `holy-moly-api`, Add to access token: On.

Ohne diesen Mapper schlägt `JWTVerifier(audience="holy-moly-api")` jedes Token zurück.

### 4.3 Rollen

| Rolle | Ort | Wer | Darf |
| --- | --- | --- | --- |
| `holy-moly-user` | Realm-Rolle | alle Nutzer, Default-Rolle des Realms | alle `convert/*`- und `tts/*`-Lese-Endpunkte, alle MCP-Tools |
| `holy-moly-admin` | Realm-Rolle | Daniel | zusätzlich `*/model/load`, `/api/v1/system/info` |

Realm-Rollen landen im Access-Token unter `realm_access.roles`; Client-Rollen unter
`resource_access.<client-id>.roles`. Dieses Dokument nutzt Realm-Rollen, weil `require_roles`
von FastMCP mit einem `extract`-Lambda arbeitet und `realm_access.roles` dort das dokumentierte
Keycloak-Beispiel ist.

### 4.4 Optional: Dynamic Client Registration für MCP-Clients

`KeycloakAuthProvider` in FastMCP setzt auf DCR: Claude Code oder VS Code registrieren sich beim
ersten Kontakt selbst als Client. Laut FastMCP-Docstring braucht das **Keycloak ≥ 26.6.0**
(Fix für MCP-kompatibles DCR). Zusätzlich muss der Realm anonyme Client-Registrierung erlauben
(Realm → Clients → Client registration → Anonymous access policies; standardmäßig verlangt
Keycloak einen Initial Access Token). Das ist eine bewusste Sicherheitsentscheidung: ein
offener DCR-Endpunkt lässt jeden Clients anlegen. Wer das nicht will, konfiguriert einen festen
Client (`holy-moly-mcp-client`, public, PKCE, Redirect-URIs `http://localhost:*/callback` bzw.
`http://127.0.0.1:*/callback`) und nutzt im Backend `RemoteAuthProvider` statt
`KeycloakAuthProvider` (siehe 5.1). Die Clients nutzen dann die vorregistrierte Client-ID.

## 5. Backend-Integration (`server/holy_moly_server/auth.py`)

Ein neues Modul kapselt alles; `web_server.py` und `mcp_server.py` importieren nur daraus.

### 5.1 Auth-Objekt bauen

```python
"""Shared authentication for REST and MCP: Keycloak OIDC + static bearer tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastmcp.server.auth import AccessToken, MultiAuth, RemoteAuthProvider, TokenVerifier
from pydantic import AnyHttpUrl
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

ROLE_USER = "holy-moly-user"
ROLE_ADMIN = "holy-moly-admin"
LOCAL_ISSUER = "holy-moly-local"


class HashedTokenVerifier(TokenVerifier):
    """Static bearer tokens, stored only as SHA-256 hashes.

    HOLY_MOLY_API_TOKENS = "<sha256hex>:<name>:<role>[,<role>]" (semikolon-getrennt)
    Beispiel: "9f86d0…:ci-runner:holy-moly-user;e3b0c4…:daniel-cli:holy-moly-user,holy-moly-admin"
    """

    def __init__(self, spec: str):
        super().__init__()
        self._tokens: dict[str, tuple[str, list[str]]] = {}
        for entry in filter(None, (e.strip() for e in spec.split(";"))):
            digest, name, roles = entry.split(":", 2)
            self._tokens[digest.lower()] = (name, roles.split(","))

    async def verify_token(self, token: str) -> AccessToken | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        for stored, (name, roles) in self._tokens.items():
            if hmac.compare_digest(stored, digest):
                return AccessToken(
                    token=token,
                    client_id=f"token:{name}",
                    scopes=[],
                    subject=f"token:{name}",
                    claims={"realm_access": {"roles": roles}, "preferred_username": name},
                )
        return None


def roles_of(claims: dict[str, Any]) -> list[str]:
    """Keycloak puts realm roles under realm_access.roles; our token verifier mirrors that."""
    return list(claims.get("realm_access", {}).get("roles", []))


def build_auth() -> MultiAuth | None:
    realm_url = os.environ.get("HOLY_MOLY_OIDC_REALM_URL")      # https://kc.example/realms/holy-moly
    public_url = os.environ.get("HOLY_MOLY_PUBLIC_URL")         # https://holy-moly.example
    audience = os.environ.get("HOLY_MOLY_OIDC_AUDIENCE", "holy-moly-api")
    token_spec = os.environ.get("HOLY_MOLY_API_TOKENS", "")
    local_jwt_secret = os.environ.get("HOLY_MOLY_LOCAL_JWT_SECRET")   # gesetzt, wenn lokale Konten aktiv
    use_dcr = os.environ.get("HOLY_MOLY_OIDC_DCR", "false").lower() == "true"

    if not realm_url and not token_spec and not local_jwt_secret:
        return None  # Auth aus: nur für lokale Entwicklung zulässig, siehe 10.1.

    server = None
    if realm_url:
        if use_dcr:
            server = KeycloakAuthProvider(realm_url=realm_url, base_url=public_url, audience=audience)
        else:
            server = RemoteAuthProvider(
                token_verifier=JWTVerifier(
                    jwks_uri=f"{realm_url}/protocol/openid-connect/certs",
                    issuer=realm_url,
                    audience=audience,
                    algorithm="RS256",
                    required_scopes=["openid"],
                ),
                authorization_servers=[AnyHttpUrl(realm_url)],
                base_url=public_url,
                resource_name="holy-moly-web",
            )

    verifiers: list[TokenVerifier] = []
    if token_spec:
        verifiers.append(HashedTokenVerifier(token_spec))
    if local_jwt_secret:
        # Verifiziert die JWTs, die /api/v1/auth/login für lokale Konten ausstellt (Abschnitt 7).
        verifiers.append(JWTVerifier(
            public_key=local_jwt_secret,       # bei HS* ist das der Shared Secret
            algorithm="HS256",
            issuer=LOCAL_ISSUER,
            audience=audience,
        ))
    return MultiAuth(server=server, verifiers=verifiers, base_url=public_url)


auth = build_auth()
```

Verifizierte Signaturen (fastmcp 4.1.0), auf die dieser Code baut:

```text
KeycloakAuthProvider(*, realm_url, base_url, required_scopes=None, audience=None, token_verifier=None)
RemoteAuthProvider(token_verifier, authorization_servers, base_url, scopes_supported=None,
                   resource_base_url=None, resource_name=None, resource_documentation=None,
                   challenge_scopes=None)
JWTVerifier(*, public_key=None, jwks_uri=None, issuer=None, audience=None, algorithm=None,
            required_scopes=None, base_url=None, ssrf_safe=False, http_client=None,
            jwks_refresh_interval=30.0)
MultiAuth(*, server=None, verifiers=None, server_source_id=None, base_url=None,
          resource_base_url=None, required_scopes=None)
TokenVerifier.verify_token(token: str) -> AccessToken | None
AccessToken: token, client_id, scopes, expires_at, resource, subject, claims, original_client_id
```

`KeycloakAuthProvider` ist intern genau ein `RemoteAuthProvider` mit einem `JWTVerifier` auf
`<realm_url>/protocol/openid-connect/certs`, Issuer `<realm_url>`, `RS256`, Default-Scope
`openid`. Der einzige Unterschied ist die DCR-Unterstützung. Beide liefern die RFC-9728-Routen.

`JWTVerifier` mit `algorithm="HS256"` erwartet den Shared Secret im Parameter `public_key`
(so dokumentiert; ein PEM-Public-Key wird dort für HS* abgelehnt). Ein Roundtrip mit einem
HS256-Token (`iss=holy-moly-local`, `aud=holy-moly-api`, `realm_access.roles`) gegen diesen
Verifier wurde am 2026-10-17 erfolgreich getestet.

Warum ein eigener `HashedTokenVerifier` statt `fastmcp…jwt.StaticTokenVerifier`: der eingebaute
hält Tokens im Klartext und trägt die Warnung "Never use this in production". Der eigene
vergleicht nur Hashes, zeitkonstant, und bildet die Rollen im selben Claim-Format ab wie
Keycloak, damit die Rollenprüfung (5.3) nicht zwischen den Quellen unterscheiden muss.

### 5.2 MCP-Tools absichern

In `mcp_server.py`:

```python
from fastmcp.server.auth import require_roles
from holy_moly_server.auth import ROLE_ADMIN, ROLE_USER, auth, roles_of

mcp = FastMCP("Holy-Moly-Converter", auth=auth)

as_user = require_roles(ROLE_USER, extract=roles_of)
as_admin = require_roles(ROLE_ADMIN, extract=roles_of)


@mcp.tool(auth=as_user)
async def convert_speech_to_text(...): ...
```

`@mcp.tool(auth=AuthCheck | list[AuthCheck])` ist in 4.1.0 vorhanden. Ein Tool, dessen Check
fehlschlägt, wird dem Client nicht einmal in `tools/list` angeboten. Innerhalb eines Tools
liefert `fastmcp.server.dependencies.get_access_token()` das `AccessToken` (z. B. für Logging
von `subject`).

Der stdio-Modus (`main()` in `mcp_server.py`) ist davon unberührt: ohne HTTP-Request gibt es
kein Bearer-Token, und FastMCP wendet die Auth-Middleware nur in `http_app()` an. Falls
`auth is None` (Entwicklung), muss `require_roles` übersprungen werden; am einfachsten, indem
`as_user`/`as_admin` dann `None` sind.

### 5.3 REST-Endpunkte absichern

FastAPI kennt FastMCPs Middleware nicht, daher eine Dependency, die dasselbe `auth`-Objekt
befragt:

```python
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


async def current_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AccessToken:
    if auth is None:
        return AccessToken(token="", client_id="dev", scopes=[], subject="dev",
                           claims={"realm_access": {"roles": [ROLE_USER, ROLE_ADMIN]}})
    if creds is None:
        raise _unauthorized(request)
    token = await auth.verify_token(creds.credentials)
    if token is None:
        raise _unauthorized(request)
    return token


def _unauthorized(request: Request) -> HTTPException:
    realm_url = os.environ.get("HOLY_MOLY_OIDC_REALM_URL", "")
    public_url = os.environ.get("HOLY_MOLY_PUBLIC_URL", "")
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Bearer token required",
        headers={"WWW-Authenticate":
                 f'Bearer resource_metadata="{public_url}/.well-known/oauth-protected-resource"'},
    )


def require_role(role: str):
    async def dep(token: AccessToken = Depends(current_token)) -> AccessToken:
        if role not in roles_of(token.claims):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Role {role} required")
        return token
    return dep


require_user = require_role(ROLE_USER)
require_admin = require_role(ROLE_ADMIN)
```

Anwendung in `web_server.py`:

| Endpunkt | Dependency |
| --- | --- |
| `POST /api/v1/convert/*`, `GET /api/v1/convert/text-to-speech/*`, `GET /api/v1/model/info`, `GET /api/v1/tts/model/info`, beide `*/model/status` | `Depends(require_user)` |
| `POST /api/v1/model/load`, `POST /api/v1/tts/model/load`, `GET /api/v1/system/info` | `Depends(require_admin)` |
| `GET /`, `/assets/*`, `/{full_path}` (SPA), `/docs`, `/openapi.json`, `/.well-known/*` | keine |

Statt jede Route einzeln zu dekorieren, bietet sich ein `APIRouter(dependencies=[Depends(require_user)])`
für `/api/v1` an, mit einem zweiten Router für die Admin-Pfade.

### 5.4 Mount in FastAPI: Well-known-Routen gehören an die Wurzel

`mcp.http_app()` hängt die Auth-Routen (inkl. `/.well-known/oauth-protected-resource…`) **in die
Sub-App**. Beim Mount unter `/mcp-server` landen sie damit unter
`/mcp-server/.well-known/…`, wo RFC 9728 sie nicht sucht. Daher zusätzlich an der Wurzel
registrieren:

```python
mcp_app = mcp.http_app(path="/mcp")
app.mount("/mcp-server", mcp_app)

if auth is not None:
    for route in auth.get_well_known_routes(mcp_path="/mcp-server/mcp"):
        app.router.routes.insert(0, route)   # vor dem SPA-Catch-all "/{full_path:path}"
```

Die Reihenfolge ist wichtig: der SPA-Fallback `@app.get("/{full_path:path}")` würde
`/.well-known/…` sonst als Frontend-Pfad behandeln und `index.html` liefern.

**Bei der Umsetzung verifizieren:** welche `resource`-URL im `WWW-Authenticate`-Header des
MCP-Endpunkts steht (401 ohne Token) und ob sie auf `<PUBLIC_URL>/mcp-server/mcp` zeigt. Der
Pfad wird aus `base_url` und dem `mcp_path` berechnet, den `http_app()` kennt, und der kennt den
Mount-Präfix nicht. Falls nötig `resource_base_url` am Provider explizit setzen.

## 6. Token-Auth für Maschinen-Clients

### 6.1 Token-Format und Ausgabe

- Format: `hm_` + 32 Bytes zufällig, base64url, z. B. `hm_3kQ…`. Das Präfix erleichtert das
  Erkennen in Logs und Secret-Scannern.
- Erzeugen und hashen auf dem Server, der Klartext wird genau einmal angezeigt:

```bash
TOKEN="hm_$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-43)"
echo "Token (einmalig!): $TOKEN"
echo "Hash: $(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)"
```

- Der Hash-Eintrag `<hash>:<name>:<rollen>` wandert nach OpenBao, Namespace `it-infra`, und wird
  im Deployment als `HOLY_MOLY_API_TOKENS` in den Container gereicht. Der Klartext geht an den
  Client (VS-Code-`inputs`, CI-Secret, cron-Env). Niemals in `mcp.json`, `.env` im Repo oder
  `docker-compose.yml` committen.
- Rotation: neuen Eintrag ergänzen, Client umstellen, alten Eintrag entfernen. Zwei gültige
  Tokens pro Name sind während der Rotation erlaubt.

### 6.2 Alternative: Keycloak Service Accounts

Statt eigener Tokens kann ein Keycloak-Client mit **Service accounts roles** (Client Credentials
Grant) ein kurzlebiges JWT holen. Vorteil: nur eine Token-Quelle, Keycloak-Revocation. Nachteil:
jeder Maschinen-Client braucht `client_id` + `client_secret` und einen Token-Refresh. Für den
`remote/`-Proxy wäre das ein Mehraufwand ohne Nutzen; für CI mit vorhandener
Keycloak-Anbindung ist es die sauberere Wahl. Beide Wege funktionieren mit dem `MultiAuth`
aus Abschnitt 5 ohne Code-Änderung, weil Service-Account-JWTs ganz normale Realm-Tokens sind
(Audience-Mapper auch für diesen Client setzen, Rolle `holy-moly-user` dem Service-Account geben).

### 6.3 `remote/`-Proxy anpassen

`mcp_remote_server.py` bekommt eine Umgebungsvariable `HOLY_MOLY_API_TOKEN` und setzt sie als
Header auf jeden Request:

```python
def _headers() -> dict[str, str]:
    token = os.environ.get("HOLY_MOLY_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}

async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=_headers()) as client:
```

In `.vscode/mcp.json` ein neuer Input vom Typ `promptString` mit `"password": true`:

```jsonc
{ "type": "promptString", "id": "holy-moly-api-token", "password": true,
  "description": "Bearer token for the remote holy-moly-web deployment" }
// ... und im Server-Block:
"env": { "HOLY_MOLY_API_TOKEN": "${input:holy-moly-api-token}" }
```

## 7. Lokale Konten (Fallback)

Mindestens ein Konto muss ohne Keycloak funktionieren: für den ersten Login nach dem Deployment,
für kleine Installationen ohne Identity Provider und als Notzugang, wenn Keycloak nicht
erreichbar ist. Lokale Konten sind bewusst schlank: Name, Passwort-Hash, Rollen. Keine
Selbstregistrierung, keine Passwort-Vergessen-Funktion, kein User-Management im UI.

### 7.1 Konfiguration

Zwei gleichwertige Quellen, Datei hat Vorrang, wenn beide gesetzt sind:

| Variable | Format | Zweck |
| --- | --- | --- |
| `HOLY_MOLY_LOCAL_USERS` | `name:<argon2-hash>:rolle[,rolle];name2:…` | direkt in Compose/Env |
| `HOLY_MOLY_LOCAL_USERS_FILE` | Pfad auf eine Datei, eine Zeile pro Konto im selben Format, `#` für Kommentare | Docker-Secret oder gemountete Datei, empfohlen |
| `HOLY_MOLY_LOCAL_JWT_SECRET` | ≥ 32 zufällige Bytes, base64 | signiert die lokal ausgestellten JWTs (HS256) |
| `HOLY_MOLY_LOCAL_TOKEN_TTL` | Sekunden, Default `28800` (8 h) | Lebensdauer eines lokalen Login-Tokens |

Beispiel-Datei `local-users.txt`:

```text
# name:argon2id-hash:rollen
admin:$argon2id$v=19$m=65536,t=3,p=4$…:holy-moly-admin,holy-moly-user
gast:$argon2id$v=19$m=65536,t=3,p=4$…:holy-moly-user
```

Hash erzeugen (Helfer im Server-Paket, Passwort wird abgefragt, nicht als Argument übergeben):

```bash
uv run --package holy-moly-server python -m holy_moly_server.local_accounts hash
# -> $argon2id$v=19$m=65536,t=3,p=4$…
openssl rand -base64 48      # -> HOLY_MOLY_LOCAL_JWT_SECRET
```

**Stolperfalle `$` in Compose:** Argon2-Hashes enthalten `$`. In `docker-compose.yml` und in
der `.env`-Interpolation von Compose muss jedes `$` als `$$` geschrieben werden, sonst wird
`$argon2id` als leere Variable ersetzt und kein Login funktioniert. Die Datei-Variante
(`HOLY_MOLY_LOCAL_USERS_FILE`) hat dieses Problem nicht und ist deshalb die Empfehlung.

Hashing: `argon2-cffi` (`argon2id`, Defaults der Bibliothek). `bcrypt` wäre ebenso vertretbar;
`sha256` ohne Salt und Work-Factor ist für Passwörter ungeeignet, auch wenn es für die
zufälligen API-Tokens aus Abschnitt 6 reicht. Beim Start prüft der Server jede Zeile auf gültiges
Hash-Format und bricht bei Fehlern ab, damit ein Tippfehler nicht erst beim Login auffällt.

Wird `HOLY_MOLY_LOCAL_USERS`/`_FILE` gesetzt, aber `HOLY_MOLY_LOCAL_JWT_SECRET` nicht, erzeugt
der Server beim Start ein zufälliges Secret und loggt eine Warnung: Logins funktionieren, aber
jeder Neustart macht alle lokalen Sessions ungültig. Für Produktion das Secret setzen.

### 7.2 Login-Endpunkt (`server/holy_moly_server/local_accounts.py`)

```python
"""Local accounts: argon2 password hashes from env/file, exchanged for short-lived HS256 JWTs."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from authlib.jose import jwt            # bereits transitiv über fastmcp vorhanden
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from holy_moly_server.auth import LOCAL_ISSUER

_hasher = PasswordHasher()


@dataclass(frozen=True)
class LocalUser:
    name: str
    password_hash: str
    roles: tuple[str, ...]


def load_local_users() -> dict[str, LocalUser]:
    path = os.environ.get("HOLY_MOLY_LOCAL_USERS_FILE")
    raw = open(path).read() if path else os.environ.get("HOLY_MOLY_LOCAL_USERS", "")
    users: dict[str, LocalUser] = {}
    for line in raw.replace(";", "\n").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, pw_hash, roles = line.split(":", 2)
        if not pw_hash.startswith("$argon2"):
            raise RuntimeError(f"local user {name!r}: password hash is not an argon2 hash")
        users[name] = LocalUser(name, pw_hash, tuple(r for r in roles.split(",") if r))
    return users


LOCAL_USERS = load_local_users()
_SECRET = os.environ.get("HOLY_MOLY_LOCAL_JWT_SECRET") or os.urandom(48).hex()
_TTL = int(os.environ.get("HOLY_MOLY_LOCAL_TOKEN_TTL", "28800"))
_AUDIENCE = os.environ.get("HOLY_MOLY_OIDC_AUDIENCE", "holy-moly-api")

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(payload: LoginRequest) -> dict[str, str | int]:
    """Exchange a local account's credentials for a short-lived bearer token."""
    user = LOCAL_USERS.get(payload.username)
    try:
        # Bei unbekanntem Nutzer trotzdem hashen, damit die Antwortzeit nichts verrät.
        _hasher.verify(user.password_hash if user else _hasher.hash("x"), payload.password)
    except VerifyMismatchError:
        user = None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    now = int(time.time())
    claims = {
        "iss": LOCAL_ISSUER,
        "aud": _AUDIENCE,
        "sub": f"local:{user.name}",
        "preferred_username": user.name,
        "realm_access": {"roles": list(user.roles)},   # gleiches Format wie Keycloak
        "iat": now,
        "exp": now + _TTL,
    }
    token = jwt.encode({"alg": "HS256"}, claims, _SECRET).decode()
    return {"access_token": token, "token_type": "Bearer", "expires_in": _TTL}


@router.get("/config")
async def auth_config() -> dict:
    """Public: tells the SPA which login methods exist. No secrets here."""
    return {
        "oidc": {"realm_url": os.environ.get("HOLY_MOLY_OIDC_REALM_URL"), "client_id": "holy-moly-web"}
        if os.environ.get("HOLY_MOLY_OIDC_REALM_URL") else None,
        "local": bool(LOCAL_USERS),
    }
```

`auth.py` liest denselben Secret-Wert; `build_auth()` hängt dafür den HS256-`JWTVerifier` an
(5.1). Rollen landen unter `realm_access.roles`, damit `roles_of()` und `require_roles` ohne
Sonderfall funktionieren. `/api/v1/auth/login` und `/api/v1/auth/config` sind die einzigen
API-Routen ohne `require_user`.

Neue Abhängigkeit in `server/pyproject.toml`: `"argon2-cffi"`. `authlib` kommt bereits mit
`fastmcp`; wer die Deprecation-Warnung von `authlib.jose` vermeiden will, nimmt `joserfc`
(ebenfalls von authlib) oder `PyJWT`.

### 7.3 Schutz des Login-Endpunkts

- **Brute-Force:** Argon2 kostet pro Versuch bewusst Rechenzeit (ca. 50–100 ms bei den
  Default-Parametern), das ist die erste Bremse. Zusätzlich ein Limit pro Client-IP und pro
  Nutzername, z. B. 10 Versuche je 15 min, in-memory reicht für eine Instanz (`slowapi` oder
  ein kleines Dict mit Zeitstempeln). Fehlversuche loggen, Erfolge auch.
- **Nur über TLS.** Passwörter laufen im Klartext im Request-Body. Lokaler Login nur hinter
  dem Reverse Proxy mit Zertifikat, in Dev nur gegen `localhost`.
- **Token-Lebensdauer.** 8 h sind ein Kompromiss, weil es keinen Refresh gibt. Kürzer heißt
  häufiger einloggen; die SPA soll beim 401 zur Login-Maske zurückkehren und den Zustand
  (gewählte Modelle, Texte) nicht verlieren.
- **Kein Revocation-Mechanismus.** Ein lokales Token bleibt bis `exp` gültig. Um alle lokalen
  Sessions zu beenden: `HOLY_MOLY_LOCAL_JWT_SECRET` rotieren und neu starten. Ein Konto entfernen
  beendet laufende Sessions ebenfalls erst nach `exp`; wer das enger braucht, prüft in
  `current_token` zusätzlich, ob `sub` noch in `LOCAL_USERS` steht.
- **Erstzugang.** Das `admin`-Konto wird beim Deployment mit einem generierten Passwort angelegt,
  der Klartext liegt in OpenBao. Es ist der Weg, der auch dann noch funktioniert, wenn
  Keycloak-Realm, Audience-Mapper oder DNS noch nicht stimmen.

### 7.4 Lokale Konten für MCP-Clients und Skripte

Ein MCP-Client kann keinen Passwort-Dialog führen. Der Weg ist zweistufig: Token per Login holen,
dann als Bearer-Header verwenden, genau wie ein statisches Token aus Abschnitt 6:

```bash
TOKEN=$(curl -s https://holy-moly.example/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"…"}' | jq -r .access_token)
claude mcp add --transport http holy-moly https://holy-moly.example/mcp-server/mcp \
  --header "Authorization: Bearer $TOKEN"
```

Das Token läuft nach `HOLY_MOLY_LOCAL_TOKEN_TTL` ab; für dauerhafte Maschinen-Zugänge bleiben
statische Tokens (Abschnitt 6) das richtige Werkzeug. Der lokale Login ist der Fallback, nicht
der Normalweg.

## 8. Frontend-Integration (Vue 3)

### 8.1 Login-Flow

Bibliothek: `oidc-client-ts` (framework-neutral, PKCE, Silent Renew) oder `keycloak-js`.
Empfehlung `oidc-client-ts`, weil es keine Keycloak-spezifische Adapter-Version nachziehen muss.

```js
// src/auth.js
import { UserManager, WebStorageStateStore } from 'oidc-client-ts'

export const userManager = new UserManager({
  authority: import.meta.env.VITE_OIDC_REALM_URL,      // https://kc.example/realms/holy-moly
  client_id: 'holy-moly-web',
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: window.location.origin,
  response_type: 'code',                               // + PKCE automatisch
  scope: 'openid profile',
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
})

export async function getAccessToken() {
  const user = await userManager.getUser()
  if (!user || user.expired) { await userManager.signinRedirect(); return null }
  return user.access_token
}
```

Ablauf:

1. App startet, `userManager.getUser()`. Kein Nutzer → `signinRedirect()` zu Keycloak.
2. Keycloak leitet auf `/callback?code=…` zurück; dort `signinRedirectCallback()`, dann
   `history.replaceState` auf `/`. Der SPA-Fallback in `web_server.py` liefert für `/callback`
   bereits `index.html`, es braucht keine Backend-Route.
3. Tokens liegen im `sessionStorage` (nicht `localStorage`: Tab-gebunden, kleinere
   XSS-Angriffsfläche). Ein Refresh-Token mit Rotation ist in Keycloak für public clients
   Standard.

**Fallback-Login in der SPA.** Beim Start lädt die App `GET /api/v1/auth/config` (öffentlich).
Daraus ergibt sich, was die Login-Ansicht zeigt:

| `oidc` | `local` | Login-Ansicht |
| --- | --- | --- |
| gesetzt | `true` | Button „Mit Keycloak anmelden" und darunter ein ausklappbares Formular „Lokales Konto" |
| gesetzt | `false` | nur der Keycloak-Button, bei OIDC-Fehler ein Hinweistext |
| `null` | `true` | nur das Formular Name/Passwort |
| `null` | `false` | Hinweis „Auth deaktiviert" (nur Dev, siehe 10.1) |

Das Formular ruft `POST /api/v1/auth/login` und legt `access_token` plus `exp` im
`sessionStorage` ab. `getAccessToken()` liefert dieses Token, solange es gültig ist, sonst
zurück zur Login-Ansicht. Der `apiFetch`-Wrapper (8.2) unterscheidet nicht zwischen den
Quellen; Keycloak-Token und lokales Token sind für ihn identisch. Bei 401 während einer
OIDC-Session: `signinRedirect()`; bei einer lokalen Session: Login-Ansicht anzeigen, ohne
die Eingaben des Nutzers zu verwerfen.

### 8.2 `fetch`-Aufrufe

Alle `fetch(`${apiBaseUrl}/api/v1/…`)`-Stellen in `App.vue` (11 Stück, Zeilen 147–721) durch
einen Wrapper ersetzen:

```js
export async function apiFetch(path, init = {}) {
  const token = await getAccessToken()
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${apiBaseUrl}${path}`, { ...init, headers })
  if (res.status === 401) await userManager.signinRedirect()
  return res
}
```

### 8.3 Das SSE-Problem

`new EventSource(url)` (Zeilen 183 und 625) kann **keine Header setzen**. Drei Optionen:

| Option | Aufwand | Bewertung |
| --- | --- | --- |
| `fetch` + `ReadableStream` (oder `@microsoft/fetch-event-source`) statt `EventSource` | gering, zwei Funktionen | **empfohlen**; Header wie überall, kein Sonderfall im Backend |
| Token als Query-Parameter `?access_token=…` | gering | Token landet in Access-Logs und Browser-History; nur mit sehr kurzer Lebensdauer vertretbar |
| Cookie-Session zusätzlich zu Bearer | hoch (CSRF, Cookie-Flags, zweiter Auth-Pfad) | nein |

Die Status-Streams enden, sobald das Modell `ready` ist, also kein Problem mit Token-Ablauf
während eines offenen Streams. Der TTS-Audio-Stream läuft bereits über `fetch` (Zeile 721) und
braucht nur den Wrapper.

### 8.4 CORS

Die SPA wird vom selben Origin wie die API ausgeliefert; CORS betrifft nur Vite-Dev
(`localhost:5173`). `allow_headers=["*"]` lässt `Authorization` bereits durch. Für Produktion
die Dev-Origins aus der Liste nehmen oder über Env steuern.

## 9. MCP-spezifische Aspekte

### 9.1 Discovery-Flow nach MCP-Authorization-Spec

1. Client ruft `POST /mcp-server/mcp` ohne Token.
2. Server antwortet `401` mit `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/mcp-server/mcp"`.
3. Client lädt die Metadata, findet `authorization_servers: ["https://kc…/realms/holy-moly"]`,
   lädt deren `/.well-known/openid-configuration`.
4. Client registriert sich per DCR (wenn erlaubt, 4.4) oder nutzt eine vorkonfigurierte
   Client-ID, startet Authorization Code + PKCE im Browser, erhält ein JWT.
5. Alle weiteren Requests tragen `Authorization: Bearer <JWT>`.

FastMCP liefert Schritt 2 und 3 aus `RemoteAuthProvider.get_routes()` (verifiziert:
`create_protected_resource_routes`). Schritt 4 ist Sache des Clients und Keycloaks.

### 9.2 Client-Konfiguration

**Claude Code, OIDC** (Discovery automatisch):

```bash
claude mcp add --transport http holy-moly https://holy-moly.example/mcp-server/mcp
```

**Claude Code, statisches Token:**

```bash
claude mcp add --transport http holy-moly https://holy-moly.example/mcp-server/mcp \
  --header "Authorization: Bearer hm_…"
```

**VS Code, statisches Token** (`.vscode/mcp.json`):

```jsonc
"holy-moly-http": {
  "type": "http",
  "url": "${input:holy-moly-api-url}/mcp-server/mcp",
  "headers": { "Authorization": "Bearer ${input:holy-moly-api-token}" }
}
```

Ohne `headers` versucht VS Code den OAuth-Flow über die Metadata; das ist der OIDC-Weg.
Ein lokales Konto wird für MCP wie ein statisches Token eingesetzt, siehe 7.4.

### 9.3 Reihenfolge der Verifier

`MultiAuth.verify_token` probiert zuerst den `server` (Keycloak), dann jeden `verifiers`-Eintrag
(verifiziert im Quelltext). Ein `hm_…`-Token ist kein JWT; der `JWTVerifier` lehnt es mit einer
Log-Zeile ab, bevor der Hash-Vergleich greift. Das ist pro Request ein fehlgeschlagener
JWT-Decode, keine Netzwerkoperation, also vernachlässigbar. Wer die Log-Zeile vermeiden will,
prüft im `HashedTokenVerifier` vorab auf das `hm_`-Präfix und ordnet ihn als ersten Verifier an;
`MultiAuth` erlaubt `verifiers` auch ohne `server`.

## 10. Konfiguration und Deployment

### 10.1 Umgebungsvariablen

| Variable | Beispiel | Pflicht | Wirkung |
| --- | --- | --- | --- |
| `HOLY_MOLY_PUBLIC_URL` | `https://holy-moly.example` | ja, sobald Auth aktiv | `base_url` für RFC-9728-Metadata und `WWW-Authenticate` |
| `HOLY_MOLY_OIDC_REALM_URL` | `https://kc.example/realms/holy-moly` | für OIDC | Issuer, JWKS, Authorization Server |
| `HOLY_MOLY_OIDC_AUDIENCE` | `holy-moly-api` | nein (Default) | `aud`-Prüfung; muss zum Keycloak-Mapper passen |
| `HOLY_MOLY_OIDC_DCR` | `false` | nein | `true` = `KeycloakAuthProvider` mit DCR, sonst `RemoteAuthProvider` |
| `HOLY_MOLY_API_TOKENS` | `<sha256>:ci:holy-moly-user;…` | für Token-Auth | statische Tokens, nur Hashes |
| `HOLY_MOLY_LOCAL_USERS` | `admin:$$argon2id$$…:holy-moly-admin,holy-moly-user` | für lokale Konten | Konten inline; `$` als `$$` escapen |
| `HOLY_MOLY_LOCAL_USERS_FILE` | `/run/secrets/holy-moly-local-users` | für lokale Konten (empfohlen) | Konten aus Datei, hat Vorrang vor `HOLY_MOLY_LOCAL_USERS` |
| `HOLY_MOLY_LOCAL_JWT_SECRET` | 48 Bytes base64 | für lokale Konten in Produktion | signiert lokale Login-Tokens; fehlt es, wird pro Start ein zufälliges erzeugt |
| `HOLY_MOLY_LOCAL_TOKEN_TTL` | `28800` | nein | Lebensdauer lokaler Login-Tokens in Sekunden |
| `HOLY_MOLY_AUTH_DISABLED` | `true` | nein | nur Dev: erlaubt Start ohne Realm, Tokens und lokale Konten; sonst Abbruch beim Start |

Startverhalten: ist keine der drei Quellen (Realm, Tokens, lokale Konten) gesetzt **und**
`HOLY_MOLY_AUTH_DISABLED` fehlt, soll `holy-moly-web` mit klarer Fehlermeldung abbrechen. Ein
versehentlich offener Dienst darf nicht leise hochkommen. Die minimale sinnvolle
Produktionskonfiguration ist ein einzelnes lokales Admin-Konto plus Secret; Keycloak und
statische Tokens kommen dazu, ohne dass sich an diesem Konto etwas ändert.

### 10.2 docker-compose

```yaml
services:
  web:
    environment:
      - HOLY_MOLY_PUBLIC_URL=${HOLY_MOLY_PUBLIC_URL}
      - HOLY_MOLY_OIDC_REALM_URL=${HOLY_MOLY_OIDC_REALM_URL}
      - HOLY_MOLY_API_TOKENS=${HOLY_MOLY_API_TOKENS}
      - HOLY_MOLY_LOCAL_JWT_SECRET=${HOLY_MOLY_LOCAL_JWT_SECRET}
      - HOLY_MOLY_LOCAL_USERS_FILE=/run/secrets/holy-moly-local-users
    secrets:
      - holy-moly-local-users

secrets:
  holy-moly-local-users:
    file: ./secrets/local-users.txt      # nicht im Repo; eine Zeile pro Konto, siehe 7.1
```

Wer die Konten lieber inline setzt (`HOLY_MOLY_LOCAL_USERS=…`), muss in Compose-Datei und
`.env` jedes `$` des Argon2-Hashes als `$$` schreiben.

Die Werte kommen aus einer `.env` neben der Compose-Datei, die nicht im Repo liegt
(`.gitignore` ergänzen), befüllt aus OpenBao (`bao kv get`). Für das Frontend wird
`VITE_OIDC_REALM_URL` zur Build-Zeit in `Dockerfile.web` als `ARG` durchgereicht, da Vite Env
beim `npm run build` einbettet. Alternative ohne Rebuild pro Umgebung: das Backend liefert
`GET /api/v1/auth/config` (öffentlich) mit Realm-URL und Client-ID, die SPA liest es beim Start.
Das ist die bessere Wahl, weil ein Image für alle Umgebungen reicht.

### 10.3 Reverse Proxy und TLS

OIDC-Redirects und Bearer-Tokens setzen TLS voraus; Keycloak lehnt `http://`-Redirect-URIs
außerhalb von `localhost` standardmäßig ab. Der Dienst gehört hinter den Nginx Proxy Manager mit
Let's-Encrypt-Zertifikat. Zu beachten:

- `X-Forwarded-Proto`/`X-Forwarded-Host` durchreichen und uvicorn mit `--proxy-headers` bzw.
  `proxy_headers=True, forwarded_allow_ips="*"` starten, sonst baut FastAPI `http://`-URLs.
- SSE und der MCP-Stream brauchen `proxy_buffering off` und lange Read-Timeouts (Konvertierungen
  dauern bis 600 s, siehe `MARKER_TIMEOUT`).
- Keycloak und `holy-moly-web` müssen sich gegenseitig erreichen: der Container lädt JWKS von der
  Realm-URL, der Browser spricht beide an.

## 11. Sicherheitsaspekte und offene Punkte

- **Audience-Prüfung nicht weglassen.** Ohne `aud` akzeptiert der Server jedes Token des Realms,
  also auch eines, das für eine andere App ausgestellt wurde.
- **Token-Lebensdauer.** Access-Tokens 5–15 min; die SPA refresht still. Statische Tokens haben
  keine Lebensdauer, daher Rotation planen (6.1) und pro Verwendungszweck ein eigenes Token.
- **Dateischreibzugriff.** Auth beschränkt den Kreis, aber `output_path` im Proxy bleibt ein
  frei wählbarer lokaler Pfad auf der Client-Maschine. Das ist Verantwortung des Nutzers, der
  den Proxy startet, und kein Serverproblem; im Dokument nur erwähnt, damit es nicht als gelöst gilt.
- **Lokaler Login ist ein Passwort-Endpunkt.** Argon2 plus Versuchslimit (7.3), nur über TLS,
  Secret in OpenBao. Ohne Refresh-Token endet eine lokale Session hart nach `exp`; das ist
  gewollt und einfacher als eine Refresh-Logik mit Revocation-Liste.
- **Rate-Limits** fehlen weiterhin. Mit `subject` aus dem `AccessToken` ist ein Limit pro Nutzer
  später ein kleiner Schritt (z. B. `slowapi`).
- **DCR offen oder zu?** Entscheidung vor dem Realm-Setup (4.4). Empfehlung: zu, fester
  MCP-Client, bis ein zweiter Nutzerkreis es braucht.
- **`host_origin_protection`.** `http_app()` in 4.1.0 kennt `allowed_hosts`/`allowed_origins`.
  Hinter dem Proxy den öffentlichen Hostnamen eintragen, sonst lehnt FastMCP Requests mit
  fremdem `Host` ab oder, je nach Default, lässt DNS-Rebinding zu. Verhalten bei der Umsetzung
  testen.
- **Verifizieren, nicht annehmen:** `WWW-Authenticate`-Resource-URL nach dem Mount (5.4),
  Keycloak-Version für DCR, und ob `require_roles` bei `auth=None` sauber übersprungen wird.

## 12. Umsetzungsplan

| Phase | Inhalt | Ergebnis |
| --- | --- | --- |
| 0 | `fastmcp>=4.1,<5` pinnen, `uv lock`, Tests grün | stabile API-Basis |
| 1 | `auth.py` mit `HashedTokenVerifier` + HS256-`JWTVerifier` + FastAPI-Dependencies; `local_accounts.py` mit `/auth/login` und `/auth/config`; REST-Endpunkte absichern; Startabbruch ohne Auth-Config; Tests für 401/403/200 und Login | REST ist geschützt, lokales Admin-Konto funktioniert, SPA bricht (erwartet) |
| 2 | Keycloak-Realm, Clients, Audience-Mapper, Rollen; `RemoteAuthProvider` in `auth.py`; Test mit echtem Token (`curl` + Token aus Keycloak-Token-Endpoint) | OIDC-Tokens werden akzeptiert |
| 3 | Frontend: Login-Ansicht (lokal + OIDC nach `/auth/config`), `oidc-client-ts`, `apiFetch`, SSE auf `fetch` umstellen | Web-UI mit Login, auch ohne Keycloak |
| 4 | MCP-HTTP-Mount (http-transport.md) mit `auth=`, Well-known-Routen an der Wurzel, `@mcp.tool(auth=…)`; Test mit Claude Code (Token) und VS Code (OIDC) | MCP über HTTP, beide Auth-Wege |
| 5 | `remote/`-Proxy: `HOLY_MOLY_API_TOKEN`; `.vscode/mcp.json`-Input; README und `remote/README.md` | Proxy bleibt nutzbar |
| 6 | Reverse Proxy + TLS, OpenBao-Einträge, Dev-Origins aus CORS entfernen | produktiv erreichbar |

Phasen 1 und 2 sind unabhängig vom HTTP-Transport und können sofort beginnen. Phase 1 allein
ergibt bereits eine nutzbare Installation mit lokalem Konto; Keycloak ist ab Phase 2 ein Zusatz. Phase 4 setzt
http-transport.md voraus.

## Verifikation (Kurzform)

```bash
BASE=https://holy-moly.example

# 1. Ohne Token: 401 mit WWW-Authenticate und resource_metadata
curl -si -X POST $BASE/mcp-server/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head -5

# 2. Metadata erreichbar und zeigt auf Keycloak
curl -s $BASE/.well-known/oauth-protected-resource/mcp-server/mcp | jq .

# 3. Statisches Token: 200
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/v1/model/info -H "Authorization: Bearer $HM_TOKEN"

# 4. Keycloak-Token holen (Test-User, nur zum Prüfen; Direct Access Grants dafür temporär an).
#    Ohne Browser und ohne Test-User geht es mit `scripts/keycloak_setup.py --verify`: temporärer
#    Service-Account-Client, client_credentials-Token, GET model/info -> 200, system/info -> 403.
KC_TOKEN=$(curl -s -d client_id=holy-moly-web -d grant_type=password \
  -d username=test -d password=… \
  https://kc.example/realms/holy-moly/protocol/openid-connect/token | jq -r .access_token)
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/v1/model/info -H "Authorization: Bearer $KC_TOKEN"

# 4b. Lokales Konto: Login liefert Token, Token wird akzeptiert
LOCAL_TOKEN=$(curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"…"}' | jq -r .access_token)
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/v1/model/info -H "Authorization: Bearer $LOCAL_TOKEN"
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"falsch"}'     # -> 401

# 5. Rolle: user darf nicht laden -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/api/v1/model/load \
  -H "Authorization: Bearer $KC_TOKEN" -H 'Content-Type: application/json' -d '{"model":"small"}'

# 6. SPA und Docs weiterhin offen
curl -s -o /dev/null -w '%{http_code}\n' $BASE/
curl -s -o /dev/null -w '%{http_code}\n' $BASE/docs
```
