# Technology Stack

**Analysis Date:** 2026-04-28

## Languages

**Primary:**
- Node.js 24 (Bookworm) - OpenClaw gateway and main runtime host
- Python 3 (system) - 42+ runtime modules for orchestration, AI routing, infra monitoring

**Secondary:**
- Bash - Startup scripts, system monitoring (`auto-heal.sh`, `ollama-check.sh`)

## Runtime

**Environment:**
- Node.js 24 (node:24-bookworm base image)
- Python 3 (system packages via apt-get)
- Container: Docker (DSM 7.3.2 on Synology DS923+)
- Host: Synology DS923+ with Ryzen Embedded R1600, 16GB RAM, 27TB storage

**Package Manager:**
- npm (Node.js) - manages openclaw CLI
- pip3 (Python) - installs runtime dependencies
- System: apt-get (Debian/Bookworm)

## Frameworks

**Core:**
- OpenClaw - Node.js 24 gateway framework (main entry point)
- Ollama - Local LLM inference (GPU: RTX 3090 @ 192.168.2.6:11434, CPU fallback: NAS @ 127.0.0.1:11434)

**Testing:**
- unittest - Python test framework (54+ test suites across runtime/)
- pytest - Test discovery and execution

**Build/Dev:**
- Docker Compose - Container orchestration (20 services total)
- Prometheus - Metrics collection (:9090)
- Grafana - Dashboarding and visualization (:3000)

## Key Dependencies

**Critical:**
- requests - HTTP library for API calls to external services
- torch - Deep learning (CPU only, installed from pytorch.org/whl/cpu)
- openai-whisper - Speech-to-text transcription (base model, Spanish)
- google-auth, google-auth-oauthlib, google-api-python-client - Google APIs (Calendar, Gmail)
- prometheus_client - Metrics export to Prometheus
- pycryptodome - Cryptography utilities
- tplinkrouterc6u - TP-Link Deco WiFi API
- python-miio - Xiaomi device control (vacuum)
- pychromecast - Google Cast device control
- websocket-client - WebSocket support for Samsung TV control

**Infrastructure:**
- himalaya CLI (v0.7+) - Email client (IMAP/SMTP for Gmail)
- curl - HTTP requests (system package)
- nmap - Network scanning (system package)
- iputils-ping, dnsutils - Network utilities (system packages)
- docker.io - Docker CLI in-container (for docker_wrapper.py)
- ffmpeg - Audio/video processing (system package)

## Configuration

**Environment:**
- `.env` file with variables: TZ, NODE_OPTIONS, OLLAMA_API_KEY, OLLAMA_API_BASE, GOG_ACCOUNT, GOOGLE_API_KEY, OPENROUTER_API_KEY, BINANCE_API_KEY, BINANCE_SECRET, GF_SECURITY_ADMIN_PASSWORD
- `state/openclaw.json` - Primary config (20KB+): auth profiles, model providers, LLM backends, Telegram settings, channel config
- `state/iol_config.json` - InvertirOnline (IOL) broker configuration
- `state/iol_credentials.json` - IOL API credentials (gitignored)

**Build:**
- `Dockerfile` - 15 lines, installs deps, adds himalaya CLI, creates tenazas user
- `docker-compose.yml` - 91 lines, defines 4 services (openclaw, prometheus, grafana, searxng)
- `prometheus.yml` - Scrape config (NAS metrics, OpenRouter, ROI, health)
- `alerts.yml` - Alert rules for Prometheus

## Platform Requirements

**Development:**
- Docker (Synology DSM compatible)
- Python 3.8+ (system)
- Node.js 24+
- SSH access to NAS (port 4822)
- Windows 11 (HomeLab server with RTX 3090)

**Production:**
- Synology DS923+ NAS (primary host)
- HomeLab GPU server 192.168.2.6 (RTX 3090, Windows 11)
- NordVPN (via gluetun container, Spanish gateway)
- ChromaDB - Vector database (:8100 on NAS)
- Piper TTS - Text-to-speech container (Spanish voice es_MX-claude-high)
- 20 Docker containers total (arr-stack × 10, Ollama, gluetun, chromadb, prometheus, grafana, etc.)

---

*Stack analysis: 2026-04-28*
