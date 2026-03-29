# CLAUDE.md — Tenazas / openclaw

Instrucciones para Claude Code al trabajar en este proyecto.

## Contexto del proyecto

Tenazas es el asistente personal de Sebastián (Sebas), corriendo en OpenClaw dentro de un contenedor Docker en un NAS Synology DS923+. El proyecto es la infraestructura del asistente, no una app web ni una librería.

**Stack:**
- NAS Synology DS923+ — host principal, Docker, Ryzen CPU
- Notebook MSI con RTX 4050 — Ollama GPU en 192.168.2.76:11434
- arr-stack (gluetun, sonarr, radarr, lidarr, prowlarr, qbittorrent, sabnzbd, tautulli, seerr, requestrr)
- VPN: NordVPN via gluetun, servidor es247.nordvpn.com (España)
- ISP: Movistar Argentina — si la VPN muestra IP argentina = leak
- ChromaDB en 192.168.2.2:8100 para RAG
- Piper TTS con voz `es_MX-claude-high`

**Archivos principales:**
- `docker-compose.yml` — definición del contenedor openclaw
- `Dockerfile` — imagen (node:22-bookworm + openclaw npm global)
- `startup.sh` — arranque: lanza gateway + notifica Telegram
- `auto-heal.sh` — auto-heal sin LLM (ver abajo)
- `ollama-check.sh` — routing híbrido local/cloud
- `state/workspace/` — workspace de Tenazas (SOUL.md, AGENTS.md, skills/, memory/)

---

## Scripts de infraestructura (sin LLM)

### `auto-heal.sh`
Corre dentro del contenedor openclaw (`docker exec openclaw bash /home/tenazas/auto-heal.sh`).
- Check VPN: si IP via proxy gluetun (192.168.2.2:8888) = Argentina → `docker restart gluetun`, espera 30s
- Check arr-stack: containers no `Up` → restart único, verifica a los 15s
- Check Ollama notebook: escribe estado en `/tmp/ollama-notebook.status`
- Notifica Telegram **solo si tomó alguna acción**
- Lock file en `/tmp/auto-heal.lock` para evitar ejecuciones paralelas
- **Cron DSM recomendado:** cada 15 min — `docker exec openclaw bash /home/tenazas/auto-heal.sh`

### `ollama-check.sh`
Routing helper con cache de 120s.
```bash
bash ollama-check.sh --route system   # → BACKEND + MODEL + ENDPOINT
bash ollama-check.sh --route reason
bash ollama-check.sh --json           # status completo
bash ollama-check.sh --status         # ok / offline
```
Cascada: Notebook GPU → NAS CPU (solo 3b) → Gemini Flash

---

## Skills implementadas en este ciclo

### `skills/compact-context/`
Compactación de contexto de sesión.
- `compact.py --summary "..."` — persiste resumen estructurado en `memory/YYYY-MM-DD.md`
- `compact.py --auto` — genera resumen con qwen2.5:3b en NAS
- `compact.py --signals` — muestra señales de contexto sucio
- Trigger automático: 2+ señales de pollution (ver AGENTS.md)

### `skills/rag/query.py` (modificado)
Cambios de reranking:
- `MIN_SCORE = 0.6` — filtro duro, resultados por debajo se descartan
- `source_boost()` — `skills/*/SKILL.md` y workspace root `.md` → +0.05 / `memory/*.md` → -0.03
- Output vacío con `--json` devuelve `[]` (antes devolvía texto)

---

## Reglas para trabajar en este repo

- **Nunca hardcodear API keys** — leerlas siempre desde `state/openclaw.json` o env vars
- **No reiniciar `openclaw` ni `watchtower`** via auto-heal ni scripts automáticos
- Los scripts que corren en el host del NAS usan `docker exec openclaw bash <script>` para aprovechar el socket y las dependencias del container
- Al modificar skills (`.md`): también actualizar `SOUL.md` si cambia el comportamiento de routing o triggers
- Al modificar `query.py` o `ingest.py`: re-indexar con `python3 ingest.py` después del deploy
- Telegram chat ID de Sebas: `1565794551` (no loguear el bot token)

---

## Tests

### Paths dentro del container
Los scripts se montan vía docker-compose volumes:
- `auto-heal.sh` → `/home/tenazas/auto-heal.sh`
- `ollama-check.sh` → `/home/tenazas/ollama-check.sh`
- `tests/` → `/home/tenazas/tests/`

Requiere `docker-compose up -d` (o `docker compose up -d`) después de cambios en compose.

### Correr todo
```bash
docker exec openclaw bash /home/tenazas/tests/run_tests.sh
```

### Correr por suite
```bash
docker exec openclaw python3 /home/tenazas/tests/test_query.py
docker exec openclaw python3 /home/tenazas/tests/test_compact.py
docker exec openclaw bash   /home/tenazas/tests/test_auto_heal.sh
docker exec openclaw bash   /home/tenazas/tests/test_ollama_check.sh
```

### Qué cubre cada suite

**test_query.py** (unittest + mock HTTP)
- `source_boost()`: boost correcto por tipo de fuente
- `MIN_SCORE = 0.6`: umbral configurado
- Filtro: resultados < 0.6 descartados
- Ranking: `SKILL.md` > `memory/*.md` con misma distancia vectorial
- JSON vacío cuando no hay resultados

**test_compact.py** (unittest + mock HTTP)
- `save_compact()`: crea archivo en fecha correcta, appends múltiples
- `generate_summary_from_logs()`: llama Ollama, maneja errores, usa qwen2.5:3b
- `print_anchor()`: output contiene ANCHOR y el resumen

**test_auto_heal.sh** (bash + mocks docker/curl)
- Todo OK → sin Telegram, sin docker restarts
- VPN leak (IP argentina) → `docker restart gluetun` + Telegram
- Gluetun DOWN → restart + Telegram
- Container arr-stack caído → restart + Telegram
- Lock file → segunda ejecución sale sin correr
- Status file de Ollama creado

**test_ollama_check.sh** (bash + mock curl)
- Notebook online + system → notebook/qwen2.5:3b
- Notebook online + reason → notebook/qwen2.5:7b
- Todo offline → cloud/gemini-flash
- Notebook offline, NAS online → nas-cpu
- Cache 120s: segunda llamada no hace ping
- Cache expirado: re-verifica
- `--json` devuelve JSON válido
