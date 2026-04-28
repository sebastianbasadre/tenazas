#!/usr/bin/env python3
"""
Classifier V7 — Multi-tier classification pipeline.

Flow:
  1. Normalize input
  2. Regex fast-path (confidence tiers: >=0.90 direct, >=0.70 candidate)
  3. Qwen3 30B via Ollama (7.5s timeout, /no_think, temp=0)
  4. Fallback: Gemma 12B → regex candidate → finetuned v6 → safe chat
  5. NEVER returns None, NEVER crashes

Integrates additively with orchestrator.py classify_query().

Usage:
    python3 classifier_v7.py --classify "prende las luces"
    python3 classifier_v7.py --test     # run test queries
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
from runtime_utils import now

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "classifier_v7.log"

# Qwen3 config (primary)
OLLAMA_BASE = OLLAMA_GPU_URL
QWEN3_MODEL = "qwen3:30b"
QWEN3_TIMEOUT = 7.5
QWEN3_SLOW_THRESHOLD = 3.0

# Gemma config (fallback)
GEMMA_MODEL = "gemma3:12b"
GEMMA_HARD_TIMEOUT = 15.0
GEMMA_SLOW_THRESHOLD = 5.0

LLM_MIN_CONFIDENCE = 0.6

# Finetuned V6 domains
V6_DOMAINS = {"infra", "arr", "smart-home", "network", "containers",
              "docker", "luces", "luz", "sonarr", "radarr", "gluetun"}

# Valid tool/skill categories
VALID_TOOLS = {
    "skill:weather", "skill:email", "skill:calendar", "skill:calendar-agenda",
    "skill:smart-home", "skill:tts", "skill:network-scanner",
    "skill:arr-monitor", "skill:infra-monitor", "skill:morning-greeting",
    "skill:self-evolve", "skill:reminders", "skill:smoking",
    "skill:smoking-tracker", "skill:expense", "skill:model-trainer",
    "skill:iol-broker",
    "chat", "knowledge_query", "infrastructure_issue",
    "automation_command", "diagnostic_request", "complex_reasoning",
}



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


# ── Step 1: Normalize ───────────────────────────────────────────


def normalize(query: str) -> str:
    """Normalize input: lowercase, strip, clean punctuation."""
    q = query.lower().strip()
    q = re.sub(r"\s+", " ", q)
    q = re.sub(r"[¿¡!?.,;:]+$", "", q).strip()
    # Common typos
    typos = {
        "apga": "apaga", "prende": "prendé", "encende": "encendé",
        "contaner": "container", "tempratura": "temperatura",
    }
    for typo, fix in typos.items():
        if typo in q:
            q = q.replace(typo, fix)
    return q


# ── Step 2: Regex fast-path ────────────────────────────────────


_CACHED_RULES = None

def _load_classification_rules() -> list:
    """Load CLASSIFICATION_RULES from orchestrator without triggering all its imports."""
    global _CACHED_RULES
    if _CACHED_RULES is not None:
        return _CACHED_RULES
    try:
        from orchestrator import CLASSIFICATION_RULES
        _CACHED_RULES = CLASSIFICATION_RULES
        return _CACHED_RULES
    except ImportError:
        pass
    # Fallback: try importlib with isolated loading
    try:
        import importlib.util
        orch_path = BASE_DIR / "orchestrator.py"
        # Read just the CLASSIFICATION_RULES from file text
        content = orch_path.read_text()
        # Find the rules section and eval it (safe: only our own code)
        start = content.index("CLASSIFICATION_RULES = [")
        # Find matching bracket
        depth = 0
        end = start
        for i, ch in enumerate(content[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        rules_text = content[start:end]
        # Execute in isolated namespace
        ns = {"re": re}
        exec(rules_text, ns)
        _CACHED_RULES = ns.get("CLASSIFICATION_RULES", [])
        return _CACHED_RULES
    except Exception:
        return []


def regex_classify(query: str) -> tuple[str | None, float]:
    """Regex-based classification. Returns (tool, confidence).

    Confidence tiers:
      >= 0.90 → direct return (skip Gemma)
      >= 0.70 → candidate (try Gemma first, use as fallback)
      < 0.70  → ignore
    """
    CLASSIFICATION_RULES = _load_classification_rules()
    if not CLASSIFICATION_RULES:
        return None, 0.0

    words = set(re.findall(r"\w+", query))
    best_cat = None
    best_score = 0
    max_possible = 0

    for category, patterns, keywords in CLASSIFICATION_RULES:
        score = 0

        # Pattern match = strong signal
        for pattern in patterns:
            if re.search(pattern, query):
                score += 3
                break

        # Keyword matches
        kw_matches = len(words & keywords)
        score += kw_matches

        # Track max possible for confidence calc
        max_possible = max(max_possible, 3 + len(keywords))

        if score > best_score:
            best_score = score
            best_cat = category

    if best_score < 2 or best_cat is None:
        return None, 0.0

    # Skill categories need score >= 3
    if best_cat.startswith("skill:") and best_score < 3:
        return None, 0.0

    # Calculate confidence: pattern(3) = 0.60, +keyword = +0.10 each
    confidence = min(best_score / 5.0, 1.0)  # 5 = high-but-reachable baseline
    return best_cat, round(confidence, 2)


# ── Step 3: Qwen3 30B (primary) ───────────────────────────────

_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a query classifier for a home automation assistant called Tenazas. "
    "Classify the following query into EXACTLY ONE of these categories:\n"
    "skill:weather, skill:email, skill:calendar, skill:smart-home, skill:tts, "
    "skill:network-scanner, skill:arr-monitor, skill:infra-monitor, "
    "skill:morning-greeting, skill:self-evolve, skill:reminders, "
    "skill:smoking, skill:expense, skill:iol-broker, "
    "chat, knowledge_query, infrastructure_issue, automation_command, "
    "diagnostic_request, complex_reasoning\n\n"
    "Respond ONLY with JSON: {\"tool\": \"category_name\", \"confidence\": 0.0-1.0} /no_think"
)


def qwen3_classify(query: str) -> tuple[str | None, float, str]:
    """Classify via Qwen3 30B on HomeLab GPU (think:false, temp=0).

    Uses /api/chat with think:false — Ollama disables the <think> tag but the
    model may still emit chain-of-thought before the JSON answer.  We parse the
    last JSON object from the response which is the actual classification.

    Returns (tool, confidence, reason).
    reason: "qwen3_success", "qwen3_slow", "qwen3_timeout", "qwen3_error"
    """
    payload = json.dumps({
        "model": QWEN3_MODEL,
        "messages": [
            {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 500},
    }).encode()

    start = time.time()
    try:
        resp = urlopen(
            Request(f"{OLLAMA_BASE}/api/chat", data=payload,
                    headers={"Content-Type": "application/json"}),
            timeout=QWEN3_TIMEOUT,
        )
        elapsed = time.time() - start
        raw = json.loads(resp.read()).get("message", {}).get("content", "")

        # Strip thinking prefix: content may contain CoT before </think> or JSON
        if "</think>" in raw:
            raw = raw.split("</think>")[-1].strip()

        tool, conf = _parse_llm_response(raw)

        if tool and tool in VALID_TOOLS and conf >= LLM_MIN_CONFIDENCE:
            reason = "qwen3_success" if elapsed < QWEN3_SLOW_THRESHOLD else "qwen3_slow"
            _log({"action": "qwen3_classify", "tool": tool, "confidence": conf,
                  "elapsed": round(elapsed, 2), "reason": reason})
            return tool, conf, reason

        _log({"action": "qwen3_low_confidence", "tool": tool, "confidence": conf,
              "elapsed": round(elapsed, 2), "raw": raw[:100]})
        return None, conf, "qwen3_low_confidence"

    except Exception as e:
        elapsed = time.time() - start
        reason = "qwen3_timeout" if elapsed >= QWEN3_TIMEOUT - 0.5 else "qwen3_error"
        _log({"action": reason, "error": str(e)[:100], "elapsed": round(elapsed, 2)})
        return None, 0.0, reason


# ── Step 3b: Gemma 12B (fallback) ─────────────────────────────


def gemma_classify(query: str) -> tuple[str | None, float, str]:
    """Classify via Gemma 12B on HomeLab GPU (fallback if Qwen3 fails).

    Returns (tool, confidence, reason).
    reason: "gemma_success", "gemma_slow_wait", "gemma_timeout", "gemma_error"
    """
    prompt = (
        f"{_CLASSIFIER_SYSTEM_PROMPT}\n\n"
        f"Query: \"{query}\"\n"
    )

    payload = json.dumps({
        "model": GEMMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 50},
    }).encode()

    start = time.time()
    try:
        resp = urlopen(
            Request(f"{OLLAMA_BASE}/api/generate", data=payload,
                    headers={"Content-Type": "application/json"}),
            timeout=GEMMA_HARD_TIMEOUT,
        )
        elapsed = time.time() - start
        raw = json.loads(resp.read()).get("response", "")

        tool, conf = _parse_llm_response(raw)

        if tool and tool in VALID_TOOLS and conf >= LLM_MIN_CONFIDENCE:
            reason = "gemma_success" if elapsed < GEMMA_SLOW_THRESHOLD else "gemma_slow_wait"
            _log({"action": "gemma_classify", "tool": tool, "confidence": conf,
                  "elapsed": round(elapsed, 2), "reason": reason})
            return tool, conf, reason

        _log({"action": "gemma_low_confidence", "tool": tool, "confidence": conf,
              "elapsed": round(elapsed, 2), "raw": raw[:100]})
        return None, conf, "gemma_low_confidence"

    except Exception as e:
        elapsed = time.time() - start
        reason = "gemma_timeout" if elapsed >= GEMMA_HARD_TIMEOUT - 0.5 else "gemma_error"
        _log({"action": reason, "error": str(e)[:100], "elapsed": round(elapsed, 2)})
        return None, 0.0, reason


def _parse_llm_response(raw: str) -> tuple[str | None, float]:
    """Parse LLM JSON response tolerantly.

    Finds the LAST JSON object in the text — important because Qwen3 with
    think:false may dump chain-of-thought before the actual JSON answer.
    """
    # Try direct JSON parse
    try:
        data = json.loads(raw.strip())
        return data.get("tool"), float(data.get("confidence", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Try extracting LAST JSON object from text (skip CoT fragments)
    matches = list(re.finditer(r'\{[^}]+\}', raw))
    for m in reversed(matches):
        try:
            data = json.loads(m.group())
            if "tool" in data:
                return data.get("tool"), float(data.get("confidence", 0))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    # Try extracting tool name directly
    for tool in VALID_TOOLS:
        if tool in raw.lower():
            return tool, 0.5

    return None, 0.0


# ── Step 5: Finetuned V6 ───────────────────────────────────────


def v6_classify(query: str) -> tuple[str | None, float]:
    """Classify via finetuned model v6. Returns (tool, confidence)."""
    try:
        from finetuned_router import route as ft_route
        from intent_normalizer import normalize_or_chat as ft_normalize
        router_out = ft_route(query)
        call = ft_normalize(router_out, query)
        if call and call.valid and call.skill != "chat":
            conf_map = {"high": 0.90, "medium": 0.70, "low": 0.40}
            conf = conf_map.get(call.confidence, 0.50)
            return call.skill, conf
    except Exception:
        pass
    return None, 0.0


# ── Step 4+6: Fallback evaluation ──────────────────────────────


def _is_v6_domain(query: str) -> bool:
    """Check if query is in a domain supported by finetuned v6."""
    words = set(re.findall(r"\w+", query.lower()))
    return bool(words & V6_DOMAINS)


# ── Main Pipeline ───────────────────────────────────────────────


def classify(query: str) -> dict:
    """Full V7 classification pipeline.

    Returns:
        {tool, confidence, reason, pipeline_steps}
        tool is NEVER None — defaults to "chat"
    """
    steps = []

    # Step 1: Normalize
    q = normalize(query)
    steps.append("normalize")

    # Step 2: Regex fast-path
    regex_tool, regex_conf = regex_classify(q)
    steps.append(f"regex:{regex_conf:.2f}")

    if regex_tool and regex_conf >= 0.90:
        _log({"action": "classify", "tool": regex_tool, "confidence": regex_conf,
              "reason": "regex_direct", "query": q[:80]})
        return {
            "tool": regex_tool, "confidence": regex_conf,
            "reason": "regex_direct", "pipeline_steps": steps,
        }

    regex_candidate = regex_tool if regex_conf >= 0.70 else None

    # Step 3: Qwen3 30B (primary)
    qwen_tool, qwen_conf, qwen_reason = qwen3_classify(q)
    steps.append(f"qwen3:{qwen_reason}")

    if qwen_tool and qwen_reason in ("qwen3_success", "qwen3_slow"):
        _log({"action": "classify", "tool": qwen_tool, "confidence": qwen_conf,
              "reason": qwen_reason, "query": q[:80]})
        return {
            "tool": qwen_tool, "confidence": qwen_conf,
            "reason": qwen_reason, "pipeline_steps": steps,
        }

    # Step 3b: Gemma 12B (fallback if Qwen3 failed)
    gemma_tool, gemma_conf, gemma_reason = gemma_classify(q)
    steps.append(f"gemma:{gemma_reason}")

    if gemma_tool and gemma_reason in ("gemma_success", "gemma_slow_wait"):
        _log({"action": "classify", "tool": gemma_tool, "confidence": gemma_conf,
              "reason": gemma_reason, "query": q[:80]})
        return {
            "tool": gemma_tool, "confidence": gemma_conf,
            "reason": gemma_reason, "pipeline_steps": steps,
        }

    # Step 4: Evaluate fallback
    steps.append("fallback_eval")

    # 4a: Regex candidate available?
    if regex_candidate:
        _log({"action": "classify", "tool": regex_candidate, "confidence": regex_conf,
              "reason": "gemma_offline_v6_fallback", "note": "regex_assist",
              "query": q[:80]})
        return {
            "tool": regex_candidate, "confidence": regex_conf,
            "reason": "gemma_offline_v6_fallback", "note": "regex_assist",
            "pipeline_steps": steps,
        }

    # 4b: V6 domain?
    if _is_v6_domain(q):
        v6_tool, v6_conf = v6_classify(q)
        steps.append(f"v6:{v6_conf:.2f}")

        if v6_tool and v6_conf >= 0.80:
            _log({"action": "classify", "tool": v6_tool, "confidence": v6_conf,
                  "reason": "gemma_offline_v6_fallback", "query": q[:80]})
            return {
                "tool": v6_tool, "confidence": v6_conf,
                "reason": "gemma_offline_v6_fallback",
                "pipeline_steps": steps,
            }
        else:
            steps.append("v6_rejected")

    # Step 6: Safe fallback
    steps.append("safe_chat")
    _log({"action": "classify", "tool": "chat", "confidence": 0.0,
          "reason": "safe_fallback", "query": q[:80]})
    return {
        "tool": "chat", "confidence": 0.0,
        "reason": "safe_fallback", "pipeline_steps": steps,
    }


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Classifier V7")
    p.add_argument("--classify", type=str, help="Classify a query")
    p.add_argument("--test", action="store_true", help="Run test queries")
    args = p.parse_args()

    if args.classify:
        result = classify(args.classify)
        print(json.dumps(result, indent=2))
    elif args.test:
        test_queries = [
            "prende las luces",
            "que temperatura hace",
            "como estan los containers",
            "hola",
            "reinicia sonarr",
            "mis emails",
            "que tengo mañana",
            "modo cine",
            "escanea la red",
            "cuanto fume hoy",
            "analiza los logs de error",
        ]
        for q in test_queries:
            r = classify(q)
            print(f"  {q:<35} → {r['tool']:<25} [{r['confidence']:.2f}] {r['reason']}")
    else:
        p.print_help()
