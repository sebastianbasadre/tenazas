#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Tenazas Ollama Check — Routing Helper
#
#  Uso:
#    source ollama-check.sh          → exporta $OLLAMA_ENDPOINT y $OLLAMA_MODEL
#    bash ollama-check.sh            → imprime JSON con resultado
#    bash ollama-check.sh --route <tipo>  → imprime el modelo a usar
#
#  Tipos de tarea para --route:
#    system   → logs, archivos, containers, JSON (usa 3b/mini)
#    reason   → razonamiento, análisis (usa 7b)
#    embed    → embeddings RAG (usa nomic-embed-text)
# ─────────────────────────────────────────────────────────────────

NOTEBOOK_URL="http://192.168.2.6:11434"
NAS_URL="http://127.0.0.1:11434"
CACHE_FILE="/tmp/ollama-notebook.status"
CACHE_TTL=120  # segundos antes de re-verificar

TIMEOUT=5

# ── Modelos por tarea ─────────────────────────────────────────────
MODEL_SYSTEM="qwen2.5:7b"
MODEL_REASON="deepseek-r1:14b"
MODEL_EMBED="nomic-embed-text:latest"
MODEL_FALLBACK_SYSTEM="phi4-mini"

# ── Check con cache ───────────────────────────────────────────────
check_notebook() {
  # Usar cache si es reciente
  if [ -f "$CACHE_FILE" ]; then
    local cache_age=$(( $(date +%s) - $(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0) ))
    if [ "$cache_age" -lt "$CACHE_TTL" ]; then
      cat "$CACHE_FILE"
      return
    fi
  fi

  # Re-verificar
  if curl -sf --max-time "$TIMEOUT" "$NOTEBOOK_URL/api/tags" > /dev/null 2>&1; then
    echo "ok" | tee "$CACHE_FILE"
  else
    echo "offline" | tee "$CACHE_FILE"
  fi
}

check_nas_ollama() {
  if curl -sf --max-time "$TIMEOUT" "$NAS_URL/api/tags" > /dev/null 2>&1; then
    echo "ok"
  else
    echo "offline"
  fi
}

get_notebook_models() {
  curl -sf --max-time "$TIMEOUT" "$NOTEBOOK_URL/api/tags" 2>/dev/null \
    | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    names = [m['name'] for m in d.get('models', [])]
    print(', '.join(names) if names else 'none')
except:
    print('error')
" 2>/dev/null || echo "error"
}

# ── Routing por tipo de tarea ─────────────────────────────────────
route_model() {
  local task_type="${1:-system}"
  local notebook_status
  notebook_status=$(check_notebook)

  if [ "$notebook_status" = "ok" ]; then
    local endpoint="$NOTEBOOK_URL"
    local model
    case "$task_type" in
      system) model="$MODEL_SYSTEM" ;;
      reason) model="$MODEL_REASON" ;;
      embed)  model="$MODEL_EMBED" ;;
      *)      model="$MODEL_SYSTEM" ;;
    esac
    echo "${endpoint}|${model}|notebook"
  else
    # Notebook offline — NAS CPU como último recurso
    local nas_status
    nas_status=$(check_nas_ollama)
    if [ "$nas_status" = "ok" ]; then
      # NAS CPU: solo modelos livianos
      echo "${NAS_URL}|${MODEL_FALLBACK_SYSTEM}|nas-cpu"
    else
      echo "cloud||gemini-flash"
    fi
  fi
}

# ── MAIN ──────────────────────────────────────────────────────────
MODE="${1:-json}"

case "$MODE" in
  --route)
    TASK="${2:-system}"
    RESULT=$(route_model "$TASK")
    ENDPOINT=$(echo "$RESULT" | cut -d'|' -f1)
    MODEL=$(echo "$RESULT" | cut -d'|' -f2)
    BACKEND=$(echo "$RESULT" | cut -d'|' -f3)

    if [ "$BACKEND" = "gemini-flash" ]; then
      echo "BACKEND=cloud"
      echo "MODEL=gemini-2.5-flash"
      echo "ENDPOINT=cloud"
      echo "ACTION=Notebook offline — usar Gemini Flash"
    elif [ "$BACKEND" = "nas-cpu" ]; then
      echo "BACKEND=nas-cpu"
      echo "MODEL=$MODEL"
      echo "ENDPOINT=$ENDPOINT"
      echo "ACTION=Notebook offline — NAS CPU (limitá tokens)"
    else
      echo "BACKEND=notebook"
      echo "MODEL=$MODEL"
      echo "ENDPOINT=$ENDPOINT"
      echo "ACTION=OK"
    fi
    ;;

  --json)
    NOTEBOOK=$(check_notebook)
    NAS=$(check_nas_ollama)

    if [ "$NOTEBOOK" = "ok" ]; then
      MODELS=$(get_notebook_models)
    else
      MODELS="offline"
    fi

    TIMESTAMP=$(TZ=America/Argentina/Buenos_Aires date +"%H:%M:%S")

    python3 -c "
import json
print(json.dumps({
    'timestamp': '$TIMESTAMP',
    'notebook': {
        'url': '$NOTEBOOK_URL',
        'status': '$NOTEBOOK',
        'models': '$MODELS'
    },
    'nas_ollama': {
        'url': '$NAS_URL',
        'status': '$NAS'
    },
    'routing': {
        'system': '$(route_model system | cut -d\"|\" -f2) @ $(route_model system | cut -d\"|\" -f3)',
        'reason': '$(route_model reason | cut -d\"|\" -f2) @ $(route_model reason | cut -d\"|\" -f3)'
    }
}, indent=2))
"
    ;;

  --status)
    # Output simple para scripts
    check_notebook
    ;;

  *)
    echo "Uso: $0 [--json|--status|--route <system|reason|embed>]"
    exit 1
    ;;
esac
