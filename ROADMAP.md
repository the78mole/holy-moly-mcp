# Roadmap

Geplante und angedachte Features für holy-moly-mcp. Erledigtes wandert nach unten in die
Historie, Details zu größeren Themen liegen in `docs/`. Stand: 2026-10-18.

## Als Nächstes

- [ ] **PDF-Templates für Markdown → PDF** – Template-Verzeichnis am Server (Jinja2-HTML + CSS +
      Logo/Fonts, per Volume eingebunden), Auswahl über Front-Matter (`template:`, `title:`, `author:`)
      oder API-Feld, gleiche Option im MCP-Tool. Erster Satz: `default`, `report` (Deckblatt,
      Inhaltsverzeichnis, Seitenzahlen), `letter` (DIN 5008), `minutes`, `plain`.
      Mermaid bleibt im Browser gerendert, das Backend bettet fertiges HTML nur ein.
- [ ] **CORS-Dev-Origins** – `localhost:5173` aus der CORS-Liste nehmen oder per Env steuern. Zugangsdaten liegen
      seit 2026-10-18 in OpenBao (`it-infra`, `secret/holy-moly-mcp`), der Test-Container ist entfernt.

## Angedacht

- [ ] **Kleineres Docker-Image** – torch aus dem CPU-Index statt PyPI; spart rund 5 GB
      (NVIDIA-CUDA-Bibliotheken, triton), der Container läuft ohnehin auf CPU.
- [ ] **Renovate für das Python-Basisimage bremsen** – `python:3.14-slim` baut nicht (marker-pdf pinnt
      Pillow 10.4.0 ohne 3.14-Wheels); Major-Updates des Image-Tags nur noch mit manuellem Build-Check.
- [ ] **TTS-Audio streamen statt puffern** – seit der Auth-Umstellung lädt das Frontend den WAV-Stream
      komplett und spielt ihn als Blob ab. Mit `MediaSource` ließe sich wieder ab dem ersten Chunk
      abspielen.
- [ ] **STT-Geschwindigkeit im Betrieb messen** – Audiodauer und Rechenzeit jeder Transkription festhalten
      und den Benchmark-Wert laufend verfeinern; vor dem Start die voraussichtliche Dauer einer Datei anzeigen.
- [ ] **Rate-Limits pro Nutzer** – `subject` aus dem Access-Token liegt vor; Limit pro Nutzer für
      Konvertierungen, etwa mit `slowapi`.
- [ ] **Audit-Log** – wer hat wann welches Tool mit welchem Modell genutzt; Identität kommt aus der
      Auth-Schicht.
- [ ] **Token-Verwaltung für Admins in der UI** – alle Tokens sehen und widerrufen (API kann es bereits).
- [ ] **Benutzerverwaltung lokaler Konten** – bisher nur per Datei; eine kleine Admin-Ansicht in der
      Web-UI zum Anlegen, Sperren und Rollen setzen.
- [ ] **Vorlagen-Upload** – eigene PDF-Templates über die Web-UI hochladen, sobald das Template-System
      steht.
- [ ] **Bilder** – der Navigationspunkt „Images" ist in der UI angelegt, aber ohne Funktion
      (OCR, Beschreibung, Konvertierung).

## Erledigt

- 2026-10-18 **MCP-Tools vollständig** – `mcp_server.py` bietet über HTTP und stdio dieselben fünf Tools wie der
  Proxy (STT, PDF → Markdown, HTML → PDF, Stimmen, TTS); Datei-Eingabe per URL, Serverpfad oder Base64, PDF und
  Audio kommen als MCP-Blobs zurück.
- 2026-10-18 **Keycloak: Umzug in den Realm `chaintronics`** – Single Sign-On mit dem AD-Konto; Rollen werden je Nutzer
  vergeben (`scripts/keycloak_setup.py --grant-user`), eigener Realm `holy-moly` gelöscht.
- 2026-10-18 **STT-Benchmark** – Admin-Knopf misst alle heruntergeladenen Whisper-Modelle mit einem
  mitgelieferten 15-s-Sample; Ergebnis „s je Minute Audio“ je Modell, Gerät und Rechentyp in SQLite, Anzeige in
  der Modellkarte. **Modell-Cache** – korrekte Erkennung (large-v3-turbo), Entfernen-Knopf für Whisper und Kokoro.
- 2026-10-18 **MCP über HTTP** – Endpunkt `/mcp-server/mcp` in der FastAPI-App, RFC-9728-Metadata, gleiche
  Auth wie die REST-API. **Persönliche API-Tokens** – pro Nutzer in der Web-UI erstellen und widerrufen,
  SQLite im Volume `holy-moly-data`.
- 2026-10-18 **Authentifizierung** – Keycloak-OIDC, statische API-Tokens, lokale Konten; Login in der
  Web-UI, Bearer-Tokens im Remote-Proxy, Realm `holy-moly` auf keycloak.bizzmark.io, Proxy-Host
  `holy-moly.intern.chaintronics.com` mit TLS. Siehe [docs/auth-oidc-and-tokens.md](docs/auth-oidc-and-tokens.md).
- 2026-10-18 **Dockerfile** – zurück auf `python:3.11-slim`, uv-Cache nicht mehr im Image, Volume für
  den marker-pdf-Modellcache.
