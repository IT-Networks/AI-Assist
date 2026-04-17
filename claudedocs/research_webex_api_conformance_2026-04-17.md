# Research: Webex API Konformität & Token-Reuse der Bot-Implementation

**Datum:** 2026-04-17  
**Scope:** v2.37.36 → 2.38.2 (Phase 1–4 des Webex-Bots)  
**Geprüfte Bereiche:** OAuth-Token-Reuse, POST /messages, Webhooks, HMAC-Signatur, Multipart-Upload, OAuth-Scopes

---

## Executive Summary

**TL;DR:** Die Implementation nutzt durchgängig den bestehenden OAuth-Token (`settings.webex.access_token`) und geht durch den gleichen `WebexClient`-Singleton wie die existierenden Read-Tools. **Kein separater Bot-Token nötig.** API-Konformität ist grün — mit **einer kritischen Lücke**: die OAuth-Scopes in der aktuellen Default-Config sind read-only (`spark:rooms_read spark:messages_read spark:people_read`) und **reichen nicht** für die neuen Write-Operationen (`POST /messages`, `POST /webhooks`).

**Confidence:** hoch für Token-Flow und HMAC (Code geprüft + offizielle Docs). Mittel für Scope-Namen: `spark:messages_write` ist laut Webex-Doku deprecated ("new integrations should use spark-compliance:messages_write instead"), die Empfehlung variiert je nach Integration-Typ (User-OAuth vs. Bot-Account vs. Service-App).

---

## 1. Token-Reuse — ✅ PASS

**Frage:** Nutzt das neue Bot-Code den gleichen OAuth-Token wie die existierenden Tools?

**Befund:** Ja. Jeder Webex-API-Call geht durch `app/services/webex_client.py::WebexClient`, das via `_get_token()` den Token aus `settings.webex.access_token` liest, persistierte Tokens aus `webex_tokens.json` nachlädt und bei Ablauf automatisch refresht (`refresh_token()`).

**Audit-Ergebnis pro Datei** (aus Code-Scan):

| Datei | Client-Aufrufe | Abweichung? |
|---|---|---|
| `app/services/webex_bot_service.py` | 12× `get_webex_client()…` | ❌ keine |
| `app/agent/webex_tools.py` | 11× `get_webex_client()…` | ❌ keine |
| `app/api/routes/webex.py` (Bot-Teil) | 7× `get_webex_client()…` | ❌ keine |
| `app/services/diagram_renderer.py` | 1× `httpx.AsyncClient(...)` | ✅ **erlaubt** — spricht kroki.io (nicht Webex), kein Token nötig |
| `WebexClient.upload_file()` | 1× `httpx.AsyncClient(...)` | ✅ **erlaubt** — multipart-Sonderweg, holt sich aber den Token via `self._get_token()` und respektiert `settings.webex.use_proxy/verify_ssl`, mit 401-Refresh-Retry |

**Bot-Identität** (`AssistRoomHandler._me`): Wird via `client.get_person_me()` aus dem gleichen authentifizierten Call geholt — Echo-Schutz vergleicht `person_id` der eingehenden Msg mit derjenigen des Tokens. **Es gibt keinen separaten `bot_token`-Field in der Config** — die einmal eingerichtete OAuth-App deckt alles ab.

---

## 2. POST /v1/messages — ✅ API-konform

**Code:** `WebexClient.send_message()` (app/services/webex_client.py)

| Parameter meiner Impl | Webex-API-Feld | Verifiziert |
|---|---|---|
| `room_id` | `roomId` | ✅ camelCase, String |
| `to_person_email` | `toPersonEmail` | ✅ |
| `to_person_id` | `toPersonId` | ✅ |
| `text` / `markdown` | `text` / `markdown` | ✅ beide optional, mind. eines erforderlich |
| `parent_id` | `parentId` | ✅ Thread-Reply |

**Gemeinsam-gesendet:** Wenn sowohl `text` als auch `markdown` gesetzt sind, rendert Webex `markdown` und nutzt `text` als Fallback für Clients ohne Markdown-Support — korrekt in meiner Impl beide als optional.

**Multipart-Upload** (`WebexClient.upload_file()`):
- Form-Field heißt `files` (genau wie Webex erwartet) — ✅
- Max-Size ~100 MB, meine Wrapper-Logik hat 90 MB Cap in `webex_share_file` — ✅ konservativ
- Content-Type via `mimetypes.guess_type()` — ✅

---

## 3. Webhooks (POST/GET/DELETE /v1/webhooks) — ✅ API-konform

**Code:** `WebexClient.register_webhook()/list_webhooks()/delete_webhook()/get_webhook()`

| Body-Feld | Webex-API | Verifiziert |
|---|---|---|
| `name` | `name` | ✅ |
| `target_url` | `targetUrl` | ✅ |
| `resource` | `resource` | ✅ `"messages"` |
| `event` | `event` | ✅ `"created"` |
| `filter` | `filter` | ✅ `roomId={bot_room_id}` — gültige Filter-Syntax |
| `secret` | `secret` | ✅ optional für HMAC |

**Filter-Syntax — zusätzliche Optimierung möglich:** Webex erlaubt Kombinationen wie `roomId=X&personEmail=owner@…`. Meine Impl nutzt nur `roomId=X` und filtert den Sender code-seitig (Allowlist). Das ist **sicherer** (bei Config-Änderung greift die Allowlist sofort, keine Webhook-Neuregistrierung nötig), aber etwas ineffizienter (API-Call für fremde Sender wird ausgelöst und dann verworfen).

---

## 4. HMAC-Signatur (X-Spark-Signature) — ✅ KORREKT

**Offizielle Doku** (Blog "Using a Webhook Secret"):
- **Algorithmus:** HMAC-**SHA1**
- **Header:** `X-Spark-Signature`
- **Encoding:** Hex **lowercase**
- **Gesigntes Datum:** **Raw Request Body** (keine Normalisierung)
- **Replay-Schutz:** keiner standardmäßig

**Meine Impl** (`AssistRoomHandler.verify_signature`):
```python
expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
return hmac.compare_digest(expected, signature.strip())
```
→ **Matcht exakt** die Referenz-Implementation:
```python
hashed = hmac.new(key.encode(), raw, hashlib.sha1)
validatedSignature = hashed.hexdigest()
```

**Neue Variante (optional, Zukunftsoption):** Webex hat 2025 zusätzlich `X-Webex-Signature` mit **HMAC-SHA256** und **HMAC-SHA512** eingeführt. Aktuell nicht nötig — `X-Spark-Signature` bleibt voll unterstützt —, aber für stärkere Security könnte man später HMAC-SHA256 ergänzen.

---

## 5. Webhook-Payload — ✅ KORREKT gehandelt

**Kritischer Webex-Fakt:** Webhook-Events liefern **nur Metadaten**, nicht den Nachrichtentext:
> „The text property is missing [from message webhooks] because message content is encrypted. You must make an authenticated GET request to /messages/{id} to retrieve full message text."

**Meine Impl:** `AssistRoomHandler.on_webhook_event` ruft genau diesen Hydration-Path auf:
```python
msg_id = data.get("id") or ""
# ... early filters ...
full_msg = await client.get_message(msg_id)    # ← korrekt: holt den Text nach
await self._dispatch_incoming(full_msg)
```
→ ✅ **Richtig gelöst**. Außerdem filtere ich fremden Room + Echo-Msg **vor** dem API-Call, spart Tokens/Rate-Limit.

**Weiterer Bonus:** Weil der Text encrypted ist, **braucht man zwingend den Token des Room-Mitglieds** (= der OAuth-User oder Bot). Das erklärt die Architektur: **der existierende OAuth-Token MUSS wiederverwendet werden**, ein fremder Token könnte den Text nicht lesen. → Token-Reuse-Design ist nicht nur sauber, sondern **alternativlos**.

---

## 6. Webhook-Lifecycle — ✅ Impl-robust

- **Disable-Policy:** Webex disabled den Webhook nach **100 Failures in 5 Minuten**.
- **Meine Impl:** Der `/webhooks/webex`-Endpoint antwortet **sofort** mit HTTP 200 (Dispatch läuft `asyncio.create_task` in Background) → Empfangsbestätigung < 200 ms → **kein Disable-Risiko**.
- **Retry-Handling durch Webex:** Bei 5xx retried Webex — Idempotenz-Guard via `todo_store.is_processed("wx-bot:v1:{msg_id}")` fängt Duplikate ab.

---

## 7. ⚠️ **KRITISCHES ACTION-ITEM: OAuth-Scopes erweitern**

**Aktuelle Default-Scopes** (`config.py::WebexConfig.scopes`):
```
spark:rooms_read spark:messages_read spark:people_read
```
Das ist **read-only**. Meine neuen Write-Operationen brauchen zusätzliche Scopes. Die Scope-Namensgebung bei Webex ist durchwachsen — folgende Optionen je nach Integrationstyp:

| Endpoint | Benötigter Scope (je nach App-Typ) |
|---|---|
| `POST /messages` (User-OAuth-Integration) | `spark:messages_write` **ODER** (neuer, empfohlen) `spark-compliance:messages_write` |
| `POST /messages` (Bot-Account) | Standardmäßig implizit erlaubt — Bot darf immer in Räumen senden, in denen er Mitglied ist |
| `POST/DELETE /webhooks` | **Kein expliziter Write-Scope**. Es reicht der Read-Scope der überwachten Resource (`spark:messages_read` deckt Messages-Webhooks ab) |
| Automatisch dazu | `spark:kms` (wird bei Integration-Registrierung automatisch gesetzt, für Entschlüsselung von Messages erforderlich) |

**Recommendation:**
- **Sichere Variante — Bot-Account** (nur wenn Webex-Integrationen mit App-Admin-Rechten möglich): eigenen Bot in `developer.webex.com` erstellen, Bot-Token direkt in `settings.webex.access_token` eintragen. Keine OAuth-Scope-Sorgen, keine 14-Tage-Refresh, Bot hat saubere Identity.
- **Einfache Variante — OAuth-User bleibt wie bisher:** in der existierenden Integration-Registrierung (developer.webex.com → My Apps → Integration-Settings) die Scopes um `spark:messages_write` erweitern, dann `config.yaml` anpassen:
  ```yaml
  webex:
    scopes: "spark:rooms_read spark:messages_read spark:messages_write spark:people_read"
  ```
  und anschließend **einmalig neu OAuth-en** (`/api/webex/oauth/url` → Login → Callback), damit der neue Token den erweiterten Scope hat.

**Gute Nachricht:** Selbst ohne Scope-Erweiterung funktionieren die Read-Tools (Phase 1-Feature "Bot-Status", Polling-Read) weiter — nur die tatsächlichen **Posts** (Send, Upload, Webhook-Register) würden mit HTTP 403 scheitern.

---

## 8. Findings-Tabelle

| Bereich | Status | Handlungsbedarf |
|---|---|---|
| Token-Reuse über `WebexClient`-Singleton | ✅ | keiner |
| OAuth-Refresh-Pfad für neue Endpoints | ✅ | keiner |
| POST /messages Payload-Felder | ✅ | keiner |
| Multipart /messages (files-Form-Field) | ✅ | keiner |
| Webhook-Create/Delete/List-CRUD | ✅ | keiner |
| X-Spark-Signature HMAC-SHA1 hex | ✅ | keiner |
| Webhook-Payload Hydration via GET /messages/{id} | ✅ | keiner |
| Webhook 200-ACK innerhalb < 200 ms | ✅ | keiner |
| Idempotenz gegen Webex-Retries | ✅ | keiner |
| **OAuth-Scopes für Write-Operationen** | ⚠️ | **Config + Re-OAuth** (siehe §7) |
| X-Webex-Signature (HMAC-SHA256) | 🔹 | optional, Zukunfts-Härtung |
| Timestamp-Replay-Protection | 🔹 | nicht von Webex standardmäßig angeboten; nur relevant wenn Bot öffentlich exponiert ist |

---

## 9. Offene Fragen / Klärung mit User

1. **App-Typ in Webex:** Hast du eine **OAuth-Integration** (User-Login-basiert) registriert oder einen **Bot-Account**? Das entscheidet über Scopes und die empfohlene Token-Quelle.
2. **Scope-Erweiterung nötig?** Willst du den OAuth-Pfad weiterfahren oder lieber auf einen Bot-Account umsteigen (cleaner für einen „immer-online"-Remote-Terminal)?
3. **Public Webhook-URL** bereits vorhanden (ngrok/Tunnel/Domain) oder bleiben wir bei Polling-Only? 

---

## 10. Quellen

- [Using a Webhook Secret — Webex Developers Blog](https://developer.webex.com/blog/using-a-webhook-secret) — HMAC-SHA1, X-Spark-Signature, Hex-Lowercase, Raw-Body
- [Webex Messaging Webhooks Guide](https://developer.webex.com/messaging/docs/api/guides/webhooks) — Payload-Struktur, Filter, Disable-Policy, fehlendes `text`-Feld
- [Webex Webhooks — Get Webhook Details](https://developer.webex.com/docs/api/v1/webhooks/get-webhook-details)
- [Webex for Developers Newsletter – January 2025](https://developer.webex.com/blog/webex-for-developers-newsletter-january-2025) — Einführung X-Webex-Signature / SHA256+SHA512
- [Webex Workspace Integrations — Technical Details](https://developer.webex.com/docs/workspace-integration-technical-details)
- [Webex Changelog](https://developer.webex.com/docs/api/changelog)
- Lokaler Code-Audit: `app/services/webex_client.py`, `app/services/webex_bot_service.py`, `app/agent/webex_tools.py`, `app/api/routes/webex.py`, `app/services/diagram_renderer.py`

---

*Report-Scope: reine Analyse, keine Code-Änderungen durchgeführt. Nächster Schritt: User entscheidet über Scope-Path (§7) — anschließend ggf. `/sc:implement` für Config-Anpassung + Scope-Dokumentation oder `/sc:design` für Bot-Account-Umstieg.*
