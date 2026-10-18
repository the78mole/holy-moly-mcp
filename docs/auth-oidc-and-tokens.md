# Authentifizierung: Keycloak (OIDC), API-Tokens und lokale Konten

Stand 2026-10-18. Beschreibt die umgesetzte Authentifizierung von `holy-moly-web` (REST-API, Web-UI
und MCP-Endpunkt über HTTP). Verworfene Alternativen stehen nicht mehr hier; die Entscheidungen und
ihre Gründe sind in Abschnitt 2 gesammelt. Für den MCP-HTTP-Transport siehe
[http-transport.md](http-transport.md).

Alle Angaben zur FastMCP-API gelten für `fastmcp` 4.1 (gepinnt auf `>=4.1,<5`).

## 1. Überblick

Vier Wege führen zu einem Bearer-Token, alle enden in derselben Prüfung:

| Weg | Wer | Token | Erstellt wo |
| --- | --- | --- | --- |
| Keycloak (OIDC, Realm `chaintronics`) | interaktive Nutzer mit AD-Konto, Single Sign-On | RS256-JWT von Keycloak, 15 min, Silent Renew | Web-UI („Mit Keycloak anmelden") oder OAuth-fähige MCP-Clients |
| Persönliche API-Tokens | Claude Code, VS Code, Proxy, Skripte | `hm_…`, nur als SHA-256-Hash gespeichert | Web-UI unter dem Benutzernamen → „API-Tokens" |
| Lokales Konto | Erstzugang, Betrieb ohne Keycloak, Notzugang | HS256-JWT vom Server, 8 h | Web-UI („Lokales Konto verwenden") oder `POST /api/v1/auth/login` |
| Statisches Token | Bootstrap ohne Web-Login | `hm_…`, Hash in `HOLY_MOLY_API_TOKENS` | von Hand (`sha256sum`) |

```text
                 Keycloak https://keycloak.bizzmark.io/realms/chaintronics
                   (JWKS, Clients holy-moly-web / holy-moly-api)
                                  ▲ Login (PKCE)        ▲ JWKS
   Vue-SPA ───────────────────────┘                      │
   Claude Code / VS Code ──── Bearer <Token> ──▶  holy-moly-web
   remote-Proxy / Skripte ─── Bearer <Token> ──▶    auth.py ── MultiAuth
                                                      ├─ RemoteAuthProvider(JWTVerifier RS256 via JWKS)   Keycloak
                                                      ├─ PersonalTokenVerifier (SQLite, Hashes)           API-Tokens
                                                      ├─ JWTVerifier HS256                                 lokale Konten
                                                      └─ HashedTokenVerifier                               statische Tokens
                                                    REST  /api/v1/*         Depends(require_user | require_admin)
                                                    MCP   /mcp-server/mcp   create_server(http=True), Tool-Checks
                                                    SPA   /                 öffentlich
                                                    RFC 9728 /.well-known/oauth-protected-resource/mcp-server/mcp
```

Rollen stehen bei jeder Quelle unter `realm_access.roles`, so wie Keycloak sie liefert:

| Rolle | Darf |
| --- | --- |
| `holy-moly-user` | alle Konvertierungs- und TTS-Endpunkte, alle MCP-Tools, eigene API-Tokens verwalten |
| `holy-moly-admin` | zusätzlich Modelle wechseln, löschen und benchmarken, `/api/v1/system/info`, alle API-Tokens sehen und widerrufen |

Öffentlich ohne Token: `/`, SPA-Assets, `/docs`, `/openapi.json`, `/api/v1/auth/config`, `/api/v1/auth/login`,
`/.well-known/*`.

## 2. Entscheidungen

| Entscheidung | Grund |
| --- | --- |
| Ein `MultiAuth`-Objekt für REST und MCP | Endpunkte werden einmal geschützt; FastMCP bekommt es über `FastMCP(auth=…)`, FastAPI über eine Dependency, die `auth.verify_token()` aufruft |
| Keycloak-Tokens lokal per JWKS prüfen, keine Introspection | kein Roundtrip pro Request; ein Keycloak-Ausfall stoppt laufende Arbeit nicht; JWKS wird gecacht |
| Gemeinsamer Realm `chaintronics` statt eigenem Realm | Single Sign-On mit dem AD-Konto; der eigene Realm `holy-moly` wurde nach dem Umzug gelöscht |
| `holy-moly-user` ist keine Default-Rolle des Realms | nicht jeder Nutzer des Sammelbeckens darf konvertieren; Rollen werden je Nutzer vergeben |
| Kein Dynamic Client Registration | ein offener DCR-Endpunkt ließe jeden Clients anlegen; `RemoteAuthProvider` mit festem Client statt `KeycloakAuthProvider` (`HOLY_MOLY_OIDC_DCR=true` schaltet um, falls gewünscht) |
| Keine Scope-Anforderung im `MultiAuth` (`required_scopes=[]`) | FastMCPs Middleware würde sonst jedes Token ohne OAuth-Scopes (API-Tokens, lokale, statische) mit `insufficient_scope` ablehnen; die Audience-Prüfung bleibt |
| Tool-Checks nur im HTTP-Modus (`create_server(http=True)`) | FastMCP blendet Tools mit `auth`-Check aus, sobald kein Bearer-Token im Kontext ist; der stdio-Server wäre toolfrei |
| Persönliche Tokens statt handgepflegter Hash-Liste | Nutzer erzeugen und widerrufen selbst; nur Hashes im Volume; `HOLY_MOLY_API_TOKENS` bleibt als Bootstrap |
| Tokens entstehen nur aus interaktiven Sitzungen | ein geleaktes Maschinentoken kann keine weiteren Zugänge prägen |
| Lokale Konten mit Argon2 und serversigniertem JWT | funktioniert ohne Keycloak; ein lokaler Nutzer sieht für Backend und MCP aus wie jeder andere |
| SSE und Audio über `fetch` statt `EventSource`/`<audio src>` | beide können keinen `Authorization`-Header senden |
| `resource_base_url` = `<PUBLIC_URL>/mcp-server` | RFC-9728-Metadata und `WWW-Authenticate` müssen den gemounteten Endpunkt nennen, nicht die Site-Wurzel |

## 3. Keycloak

Realm `chaintronics` auf `https://keycloak.bizzmark.io`, Objekte angelegt mit
[scripts/keycloak_setup.py](../scripts/keycloak_setup.py):

| Objekt | Einstellung |
| --- | --- |
| Client `holy-moly-web` | public, Standard Flow, PKCE `S256`, Redirect-URIs `https://holy-moly.intern.chaintronics.com/*` und `http://localhost:5173/*`, Web Origins ebenso |
| Client `holy-moly-api` | bearer-only, dient nur als Audience |
| Mapper am Web-Client | Audience `holy-moly-api` ins Access-Token; ohne ihn lehnt `JWTVerifier(audience="holy-moly-api")` jedes Token ab |
| Realm-Rollen | `holy-moly-user`, `holy-moly-admin`; `daniel` hat beide |

Das Skript ist idempotent und für einen gemeinsamen Realm ausgelegt: Realm-Einstellungen bleiben unangetastet
(`--manage-realm-settings` schreibt sie), die Default-Rolle bleibt unverändert (`--default-user-role` ändert das).
Admin-Zugang über den Service-Account `svc-admin` des Realms:

```bash
cd ~/GIT/Server/it-infra            # direnv: OpenBao-Token für it-infra
direnv exec . env KC_URL=https://keycloak.bizzmark.io KC_REALM=chaintronics KC_ADMIN_REALM=chaintronics \
  HOLY_MOLY_PUBLIC_URL=https://holy-moly.intern.chaintronics.com \
  secretenv run --bao secret/keycloak-svc-admin:client_id=KC_ADMIN_CLIENT_ID,client_secret=KC_ADMIN_CLIENT_SECRET \
  -- python3 ~/GIT/MCP/holy-moly-mcp/scripts/keycloak_setup.py --grant-user alice      # oder --grant-admin, --verify
```

`--verify` legt einen temporären Service-Account-Client an, holt ein Token und prüft gegen die Live-Instanz
(Nutzer-Route 200, Admin-Route 403), dann räumt es auf. Realm anlegen oder löschen (`--delete-realm`) braucht
den Master-Admin (`secret/keycloak-master-admin`, `KC_ADMIN_REALM=master`).

## 4. Backend

| Modul | Aufgabe |
| --- | --- |
| [auth.py](../server/holy_moly_server/auth.py) | `AuthConfig` aus Env, `build_auth()` → `MultiAuth`, `HashedTokenVerifier`, Dependencies `current_token`, `require_user`, `require_admin`, Startabbruch `ensure_auth_configured()`, Konstanten `MCP_MOUNT_PATH`/`MCP_PATH` |
| [token_store.py](../server/holy_moly_server/token_store.py) | SQLite-Speicher für persönliche Tokens (`HOLY_MOLY_DATA_DIR/tokens.sqlite3`, Datei 600), `PersonalTokenVerifier` |
| [api_tokens.py](../server/holy_moly_server/api_tokens.py) | `GET/POST /api/v1/auth/tokens`, `DELETE /api/v1/auth/tokens/{id}[?purge=true]` |
| [local_accounts.py](../server/holy_moly_server/local_accounts.py) | Konten aus Env/Datei, `POST /api/v1/auth/login`, `GET /api/v1/auth/config`, Versuchslimit, Hash-CLI |
| [mcp_http.py](../server/holy_moly_server/mcp_http.py) | Mount des FastMCP-HTTP-Apps und der Well-known-Routen |
| [mcp_server.py](../server/holy_moly_server/mcp_server.py) | `create_server(http=…)`: Tools, Auth-Provider und Rollen-Checks nur für HTTP |
| [web_server.py](../server/holy_moly_server/web_server.py) | Router einbinden, Lifespans verketten, jede `/api/v1`-Route mit `AS_USER` oder `AS_ADMIN` |

Reihenfolge der Prüfung in `MultiAuth`: Keycloak-Server, dann persönliche Tokens, lokale JWTs, statische Tokens.
Ein `hm_…`-Token ist kein JWT; der Keycloak-Verifier lehnt es ohne Netzwerkzugriff ab, danach greift der
Hash-Vergleich.

### Persönliche API-Tokens

- Format `hm_` + 43 Zeichen, Klartext genau einmal in der Antwort von `POST /api/v1/auth/tokens`.
  Gespeichert: SHA-256, Name, 12-stelliges Präfix, Besitzer (`sub`), Rollen, Erstellt, Ablauf, Zuletzt benutzt.
- Rollen: Teilmenge der Rollen des Besitzers zum Erstellzeitpunkt (Admins erzeugen standardmäßig Tokens ohne
  Admin-Rechte). Höchstens 50 aktive je Nutzer.
- Nur Keycloak- oder lokale Sitzungen dürfen Tokens erstellen oder widerrufen. Admins sehen mit `?all=true` alle.
- Widerruf wirkt sofort; `last_used_at` wird höchstens einmal pro Minute geschrieben.

### Lokale Konten

- `HOLY_MOLY_LOCAL_USERS_FILE` (eine Zeile `name:<argon2-hash>:rolle[,rolle]`, bevorzugt, als Compose-Secret) oder
  `HOLY_MOLY_LOCAL_USERS` inline (jedes `$` des Hashes als `$$`). Hash erzeugen:
  `python -m holy_moly_server.local_accounts hash`.
- Login tauscht Name und Passwort gegen ein HS256-JWT (`iss=holy-moly-local`, Laufzeit `HOLY_MOLY_LOCAL_TOKEN_TTL`,
  Default 8 h), signiert mit `HOLY_MOLY_LOCAL_JWT_SECRET`. Fehlt das Secret, wird pro Start ein zufälliges erzeugt
  und jede lokale Sitzung endet beim Neustart.
- Schutz: Argon2-Prüfung auch bei unbekanntem Nutzer (gleiche Antwortzeit), 10 Fehlversuche je IP und je Nutzername
  in 15 Minuten → 429. Kein Refresh, keine Revocation außer Secret-Rotation.

## 5. Frontend

[frontend/src/auth.js](../frontend/src/auth.js) liest `/api/v1/auth/config` und bietet an, was der Server hat
(Keycloak-Button, lokales Formular, beides). Keycloak läuft mit `oidc-client-ts` als Authorization Code + PKCE,
Rücksprung auf `/callback`, Tokens im `sessionStorage`, Silent Renew. `apiFetch()` hängt das Token an jeden
Aufruf; ein 401 beendet die Sitzung und zeigt die Login-Ansicht. `sseStream()` ersetzt `EventSource`, TTS-Audio
wird mit Token geladen und als Blob abgespielt. Die Sidebar zeigt Name, Admin-Badge, „API-Tokens" und
„Abmelden"; Modellwechsel, Löschen und Benchmark sind für Nicht-Admins gesperrt.

## 6. MCP-Clients

Ohne Token antwortet `/mcp-server/mcp` mit 401 und
`WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/mcp-server/mcp"`; das
Metadata-Dokument nennt den Keycloak-Realm. Mit Token:

```bash
claude mcp add --transport http holy-moly https://holy-moly.intern.chaintronics.com/mcp-server/mcp \
  --header "Authorization: Bearer ${HOLY_MOLY_TOKEN}"
```

```jsonc
// .mcp.json / .vscode/mcp.json
"holy-moly-http": {
  "type": "http",
  "url": "https://holy-moly.intern.chaintronics.com/mcp-server/mcp",
  "headers": { "Authorization": "Bearer ${HOLY_MOLY_TOKEN}" }
}
```

Der stdio-Proxy `holy-moly-mcp-remote` sendet `HOLY_MOLY_API_TOKEN` als Bearer-Header an die API und nur dorthin,
nie an Download-URLs. Der lokale stdio-Server `holy-moly-mcp` bleibt ohne Auth.

## 7. Konfiguration

| Variable | Beispiel | Wirkung |
| --- | --- | --- |
| `HOLY_MOLY_PUBLIC_URL` | `https://holy-moly.intern.chaintronics.com` | Basis für RFC-9728-Metadata und Redirects; Pflicht mit OIDC |
| `HOLY_MOLY_OIDC_REALM_URL` | `https://keycloak.bizzmark.io/realms/chaintronics` | Issuer, JWKS, Authorization Server |
| `HOLY_MOLY_OIDC_AUDIENCE` | `holy-moly-api` (Default) | `aud`-Prüfung, muss zum Keycloak-Mapper passen |
| `HOLY_MOLY_OIDC_DCR` | `false` (Default) | `true` → `KeycloakAuthProvider` mit Dynamic Client Registration |
| `HOLY_MOLY_DATA_DIR` | `/app/data` | SQLite für persönliche Tokens und Benchmarks; Compose-Volume `holy-moly-data` |
| `HOLY_MOLY_LOCAL_USERS_FILE` / `HOLY_MOLY_LOCAL_USERS` | `/run/secrets/holy-moly-local-users` | lokale Konten |
| `HOLY_MOLY_LOCAL_JWT_SECRET` | 48 Bytes base64 | signiert lokale Login-Tokens |
| `HOLY_MOLY_LOCAL_TOKEN_TTL` | `28800` | Laufzeit lokaler Tokens in Sekunden |
| `HOLY_MOLY_API_TOKENS` | `<sha256>:name:rolle[,rolle];…` | statische Tokens, nur Hashes |
| `HOLY_MOLY_AUTH_DISABLED` | `true` | nur Entwicklung: jede Anfrage gilt als Admin; sonst Startabbruch ohne Quelle |

Ohne Realm, Tokens oder lokale Konten startet `holy-moly-web` nicht, es sei denn `HOLY_MOLY_AUTH_DISABLED=true`.
Die minimale Produktionskonfiguration ist ein lokales Admin-Konto plus Secret.

## 8. Deployment auf dockerapps

- `/opt/compose/holy-moly-mcp`, Checkout von `main`. Host-Spezifisches in `docker-compose.override.yml`
  (Port 8087, Restart-Policy, Secret `secrets/local-users.txt`), Secrets in `.env`; beides nicht versioniert.
- Nginx Proxy Manager: `holy-moly.intern.chaintronics.com` → `192.168.127.127:8087`, Wildcard-Zertifikat,
  HTTPS erzwungen, `proxy_buffering off`, 900 s Timeouts, `client_max_body_size 512m`.
- Zugangsdaten (lokales Admin-Konto, statisches Token, JWT-Secret) in OpenBao `it-infra`, `secret/holy-moly-mcp`.
- Rotation: Passwort-Hash in `secrets/local-users.txt` bzw. Token-Hash in `.env` ersetzen, `docker compose up -d`,
  OpenBao-Eintrag versionieren. Persönliche Tokens widerruft der Nutzer selbst in der Web-UI.

## 9. Prüfen

```bash
B=https://holy-moly.intern.chaintronics.com
curl -s $B/api/v1/auth/config                                                  # oidc.realm_url, local: true
curl -s -o /dev/null -w '%{http_code}\n' $B/api/v1/model/info                  # 401
curl -s $B/.well-known/oauth-protected-resource/mcp-server/mcp | jq .          # resource, authorization_servers
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $HOLY_MOLY_TOKEN" $B/api/v1/model/info   # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H "Authorization: Bearer $HOLY_MOLY_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  $B/mcp-server/mcp                                                            # 200
```

Keycloak-Token end-to-end: `scripts/keycloak_setup.py --verify` (Abschnitt 3). Unit-Tests:
`server/tests/test_auth.py`, `test_api_tokens.py`, `test_mcp_http.py`, `test_mcp_server_auth.py`.

## 10. Bekannte Grenzen

- Keine Rate-Limits pro Nutzer und kein Audit-Log; die Identität (`subject`) liegt dafür bereit.
- Lokale Sitzungen enden hart nach Ablauf; es gibt keinen Refresh.
- Aliasse wie `turbo` und `large-v3-turbo` teilen Modelldateien; betrifft Modell-Cache, nicht Auth.
- CORS erlaubt weiterhin `localhost:5173` für die Vite-Entwicklung.
