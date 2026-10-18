# Roadmap

Geplante und angedachte Features für holy-moly-mcp. Erledigtes wandert nach unten in die
Historie, Details zu größeren Themen liegen in `docs/`. Stand: 2026-10-18.

## Als Nächstes

- [ ] **MCP über HTTP** – `mcp.http_app()` in die FastAPI-App mounten, mit `create_server(http=True)`
      (Auth-Provider und Rollen-Checks), Well-known-Routen für RFC 9728 an der Wurzel.
      Konzept: [docs/http-transport.md](docs/http-transport.md), Auth-Phase 4 in
      [docs/auth-oidc-and-tokens.md](docs/auth-oidc-and-tokens.md).
- [ ] **PDF-Templates für Markdown → PDF** – Template-Verzeichnis am Server (Jinja2-HTML + CSS +
      Logo/Fonts, per Volume eingebunden), Auswahl über Front-Matter (`template:`, `title:`, `author:`)
      oder API-Feld, gleiche Option im MCP-Tool. Erster Satz: `default`, `report` (Deckblatt,
      Inhaltsverzeichnis, Seitenzahlen), `letter` (DIN 5008), `minutes`, `plain`.
      Mermaid bleibt im Browser gerendert, das Backend bettet fertiges HTML nur ein.
- [ ] **Keycloak: AD-Anmeldung** – Entscheidung offen: Clients in den Realm `chaintronics` umziehen,
      Identity Brokering, oder LDAP-Federation direkt im Realm `holy-moly`. Für den Umzug muss
      `scripts/keycloak_setup.py` bestehende Realm-Einstellungen unangetastet lassen.
- [ ] **Betrieb der Auth-Schicht abschließen** (Phase 6) – API-Token und lokales Admin-Konto von der
      Credentials-Datei auf dem Host nach OpenBao/KeePass, Dev-Origins aus der CORS-Liste, Test-Container
      `holy-moly-authtest` auf dockerapps entfernen.

## Angedacht

- [ ] **Kleineres Docker-Image** – torch aus dem CPU-Index statt PyPI; spart rund 5 GB
      (NVIDIA-CUDA-Bibliotheken, triton), der Container läuft ohnehin auf CPU.
- [ ] **Renovate für das Python-Basisimage bremsen** – `python:3.14-slim` baut nicht (marker-pdf pinnt
      Pillow 10.4.0 ohne 3.14-Wheels); Major-Updates des Image-Tags nur noch mit manuellem Build-Check.
- [ ] **TTS-Audio streamen statt puffern** – seit der Auth-Umstellung lädt das Frontend den WAV-Stream
      komplett und spielt ihn als Blob ab. Mit `MediaSource` ließe sich wieder ab dem ersten Chunk
      abspielen.
- [ ] **Rate-Limits pro Nutzer** – `subject` aus dem Access-Token liegt vor; Limit pro Nutzer für
      Konvertierungen, etwa mit `slowapi`.
- [ ] **Audit-Log** – wer hat wann welches Tool mit welchem Modell genutzt; Identität kommt aus der
      Auth-Schicht.
- [ ] **PDF → Markdown im lokalen MCP-Server** – `convert_pdf_to_markdown` in `mcp_server.py` ist noch
      ein Platzhalter; der Remote-Proxy kann es bereits über die API.
- [ ] **Benutzerverwaltung lokaler Konten** – bisher nur per Datei; eine kleine Admin-Ansicht in der
      Web-UI zum Anlegen, Sperren und Rollen setzen.
- [ ] **Vorlagen-Upload** – eigene PDF-Templates über die Web-UI hochladen, sobald das Template-System
      steht.
- [ ] **Bilder** – der Navigationspunkt „Images" ist in der UI angelegt, aber ohne Funktion
      (OCR, Beschreibung, Konvertierung).

## Erledigt

- 2026-10-18 **Authentifizierung** – Keycloak-OIDC, statische API-Tokens, lokale Konten; Login in der
  Web-UI, Bearer-Tokens im Remote-Proxy, Realm `holy-moly` auf keycloak.bizzmark.io, Proxy-Host
  `holy-moly.intern.chaintronics.com` mit TLS. Siehe [docs/auth-oidc-and-tokens.md](docs/auth-oidc-and-tokens.md).
- 2026-10-18 **Dockerfile** – zurück auf `python:3.11-slim`, uv-Cache nicht mehr im Image, Volume für
  den marker-pdf-Modellcache.
