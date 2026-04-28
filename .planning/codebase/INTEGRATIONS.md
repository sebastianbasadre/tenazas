# External Integrations

**Analysis Date:** 2026-04-28

## APIs & External Services

**LLM Inference:**
- Ollama (HomeLab) - Local inference on RTX 3090 @ `http://192.168.2.6:11434`
  - Models: qwen2.5:7b, qwen2.5:32b, qwen2.5-coder:14b, deepseek-r1:14b, qwen2.5vl:7b, qwen3:8b, phi4-mini, nomic-embed-text
  - Auth: API key via env var `OLLAMA_API_KEY` (value: "ollama-local")
  - Used by: `model_router.py`, `orchestrator.py`, all LLM-dependent modules

- Ollama (NAS CPU) - CPU fallback @ `http://127.0.0.1:11434`
  - Models: qwen2.5:3b (fast), phi4-mini (lightweight only)
  - Fallback when HomeLab offline

- Google Gemini - Cloud LLM fallback
  - Models: gemini-2.5-flash (fast), gemini-2.5-pro (reasoning)
  - Auth: `GOOGLE_API_KEY` env var
  - Used by: Orchestrator V1 model fallback chain

- OpenRouter (Kimi K2) - Cloud reasoning via gateway
  - Model: moonshotai/kimi-k2 (via OpenRouter API v1 compatible)
  - Auth: `OPENROUTER_API_KEY` env var
  - Used by: self-evolve.py (weekly analysis), fallback chain
  - Cost: $0.60/Mtok input, $2.40/Mtok output

## Data Storage

**Databases:**
- ChromaDB @ `http://192.168.2.2:8100` - Vector DB for RAG
  - Collection: tenazas-knowledge
  - Embeddings: nomic-embed-text via local Ollama
  - Ingestion: every 6 hours, sources = SOUL.md + SKILL.md files + workspace/memory/

**File Storage:**
- Local filesystem (Docker volumes)
  - `./state` → `/home/tenazas/.openclaw` (config, credentials, sessions, identity)
  - `./workspace/runtime` → `/home/tenazas/runtime` (Python modules, state files)
  - `/volume1/docker/piper/data` → `/home/tenazas/piper` (TTS audio cache)

**Caching:**
- In-memory: conversation history (10 turn sliding window, 10 min TTL per chat)
- File-based state: JSON files in `state/`, `workspace/runtime/` (*.json)
- Ollama status cache: `/tmp/ollama-notebook.status` (120s TTL)

## Authentication & Identity

**Auth Providers:**

- **Google OAuth** - Calendar (Google Calendar API)
  - Client credentials: `credentials/google_oauth_credentials.json` (gitignored)
  - Refresh token: `state/gcal-token.json` (persisted)
  - Scopes: calendar.readonly
  - Used by: `gcal.py` (calendar skill)

- **Google API Key** - Gemini LLM access
  - Key: `GOOGLE_API_KEY` env var
  - Used by: orchestrator.py (fallback cloud LLM)

- **Telegram Bot** - Chat interface
  - Token: stored in `state/openclaw.json` → `channels.telegram.botToken`
  - Chat ID: 1565794551 (Sebas's personal chat)
  - Policy: allowlist (only Sebas)
  - Used by: telegram_router.py (polling bot), exec_approvals.py (inline buttons)

- **Himalaya CLI** - Email authentication
  - Config: `himalaya-config/` (IMAP/SMTP for Gmail)
  - Credentials: stored in himalaya config (gitignored)
  - Used by: email skill (digest queries)

- **Binance API** - Crypto trading
  - API Key: `BINANCE_API_KEY` env var
  - API Secret: `BINANCE_SECRET` env var
  - Used by: finance_exporter.py (portfolio metrics)
  - Route: via gluetun VPN proxy @ 192.168.2.2:8888

- **InvertirOnline (IOL)** - Equity broker API
  - Credentials: `state/iol_credentials.json` (bearer token + refresh token, gitignored)
  - Config: `state/iol_config.json`
  - API endpoint: https://api.invertironline.com/ (https)
  - Used by: finance_exporter.py, agent_finance.py, trading modules
  - Rate limit: 25,000 calls/month

- **OpenRouter API Key** - Cloud model gateway
  - Key: `OPENROUTER_API_KEY` env var
  - Models: moonshotai/kimi-k2, google/gemini-flash-1.5
  - Used by: self-evolve.py (weekly), fallback chain

## Monitoring & Observability

**Error Tracking:**
- None detected (errors logged to JSON JSONL files in `logs/`)
- Crash capture: `logs/runtime_crash.json` (devin_lite.py)

**Logs:**
- JSONL format (one entry per line) in `workspace/runtime/logs/`
- Rotation: 5MB max per file, 5 backups retained
- Key logs: orchestrator.log, model_router.log, telegram_router.log, runtime.log, health_sweep.log
- Persistence: append-only, immutable

**Metrics Export:**
- Prometheus @ `http://127.0.0.1:9090/metrics` (native format)
- Finance metrics @ `:9109` (custom exporter: finance_exporter.py)
- Fallback: JSON export to `logs/runtime_metrics.log` if prometheus_client unavailable
- Scrapers: prometheus.yml pulls from openclaw (:9105), binance (:9106), models (:9107), finance (:9109)

**Dashboards:**
- Grafana @ `:3000` (admin/password from GF_SECURITY_ADMIN_PASSWORD)
- Dashboards: tenazas-runtime, tenazas-agent, tenazas-device-discovery, tenazas-roi, iol-portfolio-analista, tenazas-infra
- Retention: 30 days, 1GB max (Prometheus)

## CI/CD & Deployment

**Hosting:**
- Synology DS923+ (Docker on DSM 7.3.2)
- docker-compose up -d (manual deployment)
- watchtower container (auto-updates images daily)

**CI Pipeline:**
- None detected (manual deployment via SSH)
- Health check: HTTP GET to `http://127.0.0.1:18789/health` (60s interval, 3 retries)

## Environment Configuration

**Required env vars (in .env or docker-compose.yml):**
- `TZ` - Timezone (America/Buenos_Aires)
- `NODE_OPTIONS` - Node.js flags (--dns-result-order=ipv4first)
- `OLLAMA_API_KEY` - Local Ollama auth (value: ollama-local)
- `OLLAMA_API_BASE` - Ollama endpoint (http://192.168.2.6:11434)
- `GOOGLE_API_KEY` - Gemini LLM access
- `OPENROUTER_API_KEY` - OpenRouter gateway (Kimi K2)
- `BINANCE_API_KEY`, `BINANCE_SECRET` - Binance trading
- `GOG_ACCOUNT`, `GOG_KEYRING_PASSWORD` - GOG CLI (legacy)
- `GF_SECURITY_ADMIN_PASSWORD` - Grafana admin password

**Secrets location:**
- `.env` file (in repo root, gitignored)
- `state/` directory (gitignored except for non-secret configs)
- `credentials/` directory (gitignored)
- Docker environment: injected via docker-compose.yml

## Webhooks & Callbacks

**Incoming:**
- Telegram Bot API - Polling (telegram_router.py, 30s timeout)
- exec_approvals.py - Telegram callback queries (inline keyboard buttons)
- No outbound webhooks configured

**Outgoing:**
- Telegram sendMessage - Notifications from various modules (runtime.py, health_sweep.py, approval_queue.py)
- No HTTP webhooks to external systems

## Connected Devices & Services

**Smart Home:**
- Philips Hue Bridge @ 192.168.2.11 (REST API)
  - 8 light fixtures, 5 scenes
  - Auth: HUE_API_KEY env var
  - Used by: devices/hue_lights.py

- Samsung M8 Monitor @ 192.168.2.46 (WebSocket :8001)
  - Control: power, volume, input, apps
  - Used by: devices/samsung_tv.py

- Marantz CINEMA 60 AVR @ 192.168.2.80 (HTTP, self-signed)
  - Control: power, volume, input, surround, mute
  - Used by: devices/marantz_avr.py

- Hue Sync Box @ 192.168.2.36 (HTTPS + Bearer token)
  - Control: sync mode, intensity, HDMI input
  - Used by: devices/hue_sync_box.py

- Google Cast (Nest Mini, Home Mini) @ 192.168.2.62, .83 (pychromecast)
  - Control: play/pause, volume, apps
  - Used by: devices/google_cast.py

- Xiaomi Vacuum S20+ @ 192.168.2.99 (python-miio, token-based)
  - Control: start, stop, dock, fan speed
  - Auth: token in openclaw.json → `devices.xiaomi_vacuum.token` (pending)
  - Used by: devices/xiaomi_vacuum.py

- NVIDIA SHIELD TV @ 192.168.2.35 (androidtvremote2)
  - Control: power (WOL), apps, media, navigation
  - Auth: certificate pairing (pending)
  - Used by: devices/shield_tv.py

**Network & Cameras:**
- EZVIZ Cameras (10 devices, various IPs) - ping only (status check)
- Dahua/Hikvision Cameras - ping only (status check)
- Nest Doorbell @ 192.168.2.22 - ping only (status check)
- TP-Link Deco B63 WiFi @ 192.168.2.1 (HTTPS API)
  - WiFi clients, band, signal strength per device
  - Used by: deco_client.py

**Network Access:**
- VPN: NordVPN via gluetun @ 192.168.2.2:8888 (HTTP proxy)
  - Gateway: es247.nordvpn.com (Spain)
  - Used by: Binance API (signed requests routed through VPN)

**Public APIs (no auth required):**
- Open-Meteo (weather) - https://api.open-meteo.com/v1/forecast (public, gratis)
- CoinGecko (crypto data) - https://api.coingecko.com/api/v3/global (public)
- alternative.me (Fear & Greed Index) - https://alternative.me/fng/ (public)
- ip-api.com (geolocation) - https://ip-api.com/json (public, rate limited)
- wttr.in (weather fallback) - https://wttr.in/ (public)

---

*Integration audit: 2026-04-28*
