# Infraestructura Tenazas — Estado actual

## Hardware

### NAS — Synology DS923+ (192.168.2.2)
- CPU: AMD Ryzen Embedded R1600
- RAM: 16 GB (15Gi)
- Storage: 27T volume1 (39% usado) + 1.8T volume2 (1% usado)
- OS: Synology DSM 7.3.2 (build 86009)
- Docker: 24.0.2
- Rol: Docker host principal, Tenazas corre acá

### HomeLab (192.168.2.6)
- Nombre: HOMELAB
- CPU: Intel Core i7-12700KF (12 cores, 20 threads, 3600MHz)
- RAM: 16 GB (15.8 GB disponibles)
- Placa madre: MSI MPG Z690 EDGE WIFI DDR4 (MS-7D31)
- GPU: NVIDIA RTX 3090 — 24GB VRAM
- OS: Windows 11 Pro (Build 26200)
- Ollama: http://192.168.2.6:11434
- Rol: GPU inference (modelos principales), training

### NAS Backup (192.168.2.3)
- HyperBackup target
- Daily 2-3AM

---

## Containers Docker (NAS) — 20 activos

| Container | Imagen | Función | Estado típico |
|---|---|---|---|
| openclaw | openclaw-openclaw | Tenazas (asistente, network_mode: host) | healthy |
| gluetun | qmcgaw/gluetun | VPN gateway NordVPN (es247) | healthy |
| qbittorrent | linuxserver/qbittorrent | Torrent client (via gluetun) | Up |
| sonarr | linuxserver/sonarr | TV series automation | Up |
| radarr | linuxserver/radarr | Movies automation | Up |
| lidarr | linuxserver/lidarr | Music automation | Up |
| prowlarr | linuxserver/prowlarr | Indexer manager | Up |
| sabnzbd | linuxserver/sabnzbd | Usenet downloader | Up |
| seerr | seerr-team/seerr | Media requests UI | Up |
| requestrr | darkalfx/requestrr | Media request bot (Discord/Telegram) | Up |
| tautulli | tautulli/tautulli | Plex stats/monitoring | healthy |
| chromadb | chromadb/chroma | Vector DB para RAG (:8100) | Up |
| piper | linuxserver/piper | TTS local (es_MX-claude-high) | Up |
| prometheus | prom/prometheus | Métricas (:9090, network_mode: host) | Up |
| grafana | grafana/grafana | Dashboards (:3000, admin/tenazas) | Up |
| portainer | portainer/portainer-ee | Docker dashboard web | Up |
| WATCHTOWER | nickfedor/watchtower | Auto-update de imágenes | healthy |
| hireswarm-agent-server | dev-agent-pipeline | HireSwarm AI agent server | healthy |
| hireswarm-inngest | inngest/inngest | HireSwarm event engine | Up |
| backing-track-generator | backing-track-generator | Generador de backing tracks | Up |

### Arr-stack (10 containers, dependen de gluetun)
gluetun, qbittorrent, sonarr, radarr, lidarr, prowlarr, sabnzbd, seerr, requestrr, tautulli

### Containers protegidos (no auto-restart)
openclaw, watchtower

---

## Puertos de servicio

| Puerto | Servicio | Notas |
|---|---|---|
| :3000 | Grafana | admin/tenazas |
| :8100 | ChromaDB | Vector store RAG |
| :8888 | Gluetun proxy | VPN HTTP proxy |
| :9090 | Prometheus | Scrape cada 30s, retención 30d |
| :9105 | metrics_server.py | Runtime + NAS + Ollama + IOL + OpenRouter |
| :9106 | api_call_counter | Gemini + IOL call tracking |
| :9107 | eval_exporter.py | Eval F1/precision/recall per model |
| :11434 | Ollama NAS (local) | CPU only, fallback |
| :18789 | OpenClaw gateway | Web canvas |

---

## APIs configuradas

| Servicio | Auth | Uso |
|---|---|---|
| Google Gemini | API Key (env) | LLM cloud fallback |
| OpenRouter (Kimi K2) | API Key (env) | Cloud fallback + self-evolve |
| Google Calendar | OAuth token | Eventos próximos |
| Gmail (himalaya) | App password | Email digest |
| Telegram Bot | Bot token (config) | Canal principal con Sebas |
| Philips Hue | API key | 8 luces smart home |
| Open-Meteo | Sin auth | Clima (gratis) |
| IOL InvertirOnline | Bearer token (auto-refresh 15min) | Portfolio, cotizaciones |

---

## LLM Routing (cascade real)

```
1. HomeLab GPU (192.168.2.6:11434) — qwen2.5:7b (default), deepseek-r1:14b, qwen2.5:32b
2. NAS CPU (127.0.0.1:11434) — phi4-mini (solo modelos chicos)
3. OpenRouter (Kimi K2) — cloud fallback ($0.60/Mtok in)
4. Gemini Flash — último recurso
```

Router V2 clasifica por rol (fast/reason/heavy/code/vision/embed) y selecciona modelo óptimo.

---

## Training Pipeline

### Estado actual
- Dataset: 306 ejemplos (245 train / 61 eval)
- Modelo base: qwen2.5-3b-instruct
- Fine-tuned: tenazas-multitool (LoRA → merge → GGUF q8_0)
- Router: tenazas-router-labels (GGUF, labels cerradas)

### Pipeline
```
build_multitool_dataset.py → split_dataset.py → train_multitool.py
→ merge_3b.py → convert_to_gguf.bat → ollama create → eval_runner.py
```

### Herramientas (HomeLab)
- Python 3.14 (3.11 bloqueado por WDAC)
- llama.cpp b4924
- GGUF outtype: q8_0
- HF_HOME: D:\hf_cache

### Eval results
| Métrica | Baseline (qwen2.5:7b) | Finetuned (tenazas-multitool) |
|---|---|---|
| F1 | 0.683 | 0.683 |
| Precision | 0.683 | 0.683 |
| Recall | 0.697 | 0.697 |
| Exact match | 0.656 | 0.656 |
| Hallucination | 0.0 | 0.0 |
| Over-trigger | 0.098 | 0.098 |
| Under-trigger | 0.164 | 0.164 |

### Per-tool F1 (baseline)
| Tool | F1 |
|---|---|
| calendar | 1.0 |
| email | 1.0 |
| smart-home-work | 1.0 |
| infra-monitor | 0.941 |
| weather | 0.909 |
| smart-home-on | 0.667 |
| tts | 0.615 |
| arr-monitor | 0.5 |
| network_scan | 0.0 |
| self-evolve | 0.0 |
| smart-home-cinema | 0.0 |
| smart-home-relax | 0.0 |

---

## Observabilidad

### Dashboards Grafana
| Dashboard | UID | Función |
|---|---|---|
| Tenazas Runtime | tenazas-runtime | Infra, NAS host, Ollama, Telegram, IOL |
| Tenazas Agent | tenazas-agent | Routing V1/V2, guardrails, latencia |
| Portfolio IOL | iol-portfolio-analista | Valor, P&L, freshness, evolución |
| Tenazas APIs | tenazas-apis | Gemini calls, OpenRouter gasto/balance |
| Multitool Eval | tenazas-multitool-eval | F1, precision, recall, per-tool, delta |
| OpenRouter / Kimi-K2 | openrouter-kimi-k2 | Gasto, balance, último check |

### Alertas Prometheus (alerts.yml)
| Alerta | Expr | Severity |
|---|---|---|
| TenazasNetworkScanStale | scan >15min | warning |
| TenazasIOLTokenExpired | IOL data >10min | warning |
| TenazasEvalExporterDown | exporter_up==0 | warning |
| TenazasEvalF1BelowThreshold | F1 finetuned <0.5 | critical |
| TenazasEvalFinetunedWorseThanBaseline | finetuned < baseline | warning |
| TenazasEvalFileStale | eval >24h | warning |

---

## Tests (36 suites)

```bash
docker exec openclaw bash /home/tenazas/tests/run_tests.sh
```

---

## Último relevamiento
Fecha: 2026-04-09
