#!/usr/bin/env python3
"""
Dataset Builder — Creates datasets from operational data.

Sources: diagnostics logs, agent reasoning logs, metrics.
Builds structured entries: {input, expected_output, source, quality_score}.
Stores in datasets/ directory (one JSON per dataset type).

Safety: Read-only. Only reads existing logs and metrics.

Log: workspace/runtime/logs/dataset.log
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATASETS_DIR = BASE_DIR / "datasets"
LOGS_DIR = BASE_DIR / "logs"
METRICS_DIR = BASE_DIR / "metrics"
TELEMETRY_LOG = LOGS_DIR / "dataset.log"

# Config
MAX_ENTRIES_PER_DATASET = 500
MIN_QUALITY_SCORE = 0.3


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _load_log_lines(log_path: Path, max_lines: int = 1000) -> list:
    """Load entries from a JSON-line log file."""
    lines = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return lines[-max_lines:]


def _load_metrics(days: int = 7) -> list:
    """Load metric entries from the last N days."""
    entries = []
    for i in range(days):
        day = _now() - timedelta(days=i)
        path = METRICS_DIR / f"{day.strftime('%Y-%m-%d')}.json"
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                entries.extend(data)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return entries


# ── Quality Scoring ─────────────────────────────────────────────


def _score_diagnostic_entry(entry: dict) -> float:
    """Score quality of a diagnostic log entry for training."""
    score = 0.5  # Base score

    # Has structured issues with severity
    issues = entry.get("issues", [])
    if issues and all("severity" in i and "problem" in i for i in issues):
        score += 0.2

    # Has action context
    if entry.get("type") == "action_mapping":
        score += 0.1

    # Has clear event counts
    if "issues_count" in entry and "critical" in entry:
        score += 0.1

    # Penalize entries with no useful content
    if not issues and entry.get("issues_count", 0) == 0:
        score -= 0.2

    return round(min(max(score, 0.0), 1.0), 4)


def _score_reasoning_entry(entry: dict) -> float:
    """Score quality of a reasoning log entry for training."""
    score = 0.4  # Base score

    # Successful reasoning
    if entry.get("status") == "complete":
        score += 0.3

    # Multiple agents used
    agents = entry.get("agents_used", [])
    if len(agents) >= 2:
        score += 0.1
    if len(agents) >= 3:
        score += 0.1

    # No fallback = cleaner data
    if not entry.get("fallback_used"):
        score += 0.1

    return round(min(max(score, 0.0), 1.0), 4)


# ── Dataset Builders ────────────────────────────────────────────


def build_diagnostic_dataset() -> dict:
    """Build a dataset from diagnostic logs.

    Each entry: {input, expected_output, source, quality_score}
    Input = system state snapshot, Output = diagnostic issues found.

    Returns: {entries: list, count: int, avg_quality: float}
    """
    diag_log = LOGS_DIR / "diagnostics.log"
    lines = _load_log_lines(diag_log)

    entries = []
    for entry in lines:
        if entry.get("type") != "diagnostics":
            continue

        quality = _score_diagnostic_entry(entry)
        if quality < MIN_QUALITY_SCORE:
            continue

        issues = entry.get("issues", [])
        issues_text = "; ".join(
            f"[{i.get('severity', '?')}] {i.get('problem', '')}"
            for i in issues
        ) if issues else "No issues detected"

        dataset_entry = {
            "input": json.dumps({
                "issues_count": entry.get("issues_count", 0),
                "critical": entry.get("critical", 0),
                "warning": entry.get("warning", 0),
                "info": entry.get("info", 0),
            }),
            "expected_output": issues_text[:500],
            "source": "diagnostics.log",
            "quality_score": quality,
            "ts": entry.get("ts", ""),
        }
        entries.append(dataset_entry)

    entries = entries[-MAX_ENTRIES_PER_DATASET:]

    avg_quality = (
        sum(e["quality_score"] for e in entries) / len(entries) if entries else 0.0
    )

    result = {
        "dataset_type": "diagnostic",
        "entries": entries,
        "count": len(entries),
        "avg_quality": round(avg_quality, 4),
        "built_at": _now().isoformat(),
    }

    # Save to datasets directory
    _save_json(DATASETS_DIR / "diagnostic_dataset.json", result)

    _log({
        "type": "dataset_built",
        "dataset": "diagnostic",
        "entries": len(entries),
        "avg_quality": round(avg_quality, 4),
    })

    return result


def build_reasoning_dataset() -> dict:
    """Build a dataset from multi-agent reasoning logs.

    Each entry: {input, expected_output, source, quality_score}
    Input = query/problem, Output = reasoning conclusion.

    Returns: {entries: list, count: int, avg_quality: float}
    """
    reasoning_log = LOGS_DIR / "multi_agent.log"
    lines = _load_log_lines(reasoning_log)

    entries = []
    for entry in lines:
        if entry.get("type") != "reasoning":
            continue

        quality = _score_reasoning_entry(entry)
        if quality < MIN_QUALITY_SCORE:
            continue

        query = entry.get("query", "")
        if not query:
            continue

        dataset_entry = {
            "input": query[:300],
            "expected_output": json.dumps({
                "status": entry.get("status", ""),
                "agents_used": entry.get("agents_used", []),
                "fallback_used": entry.get("fallback_used", False),
            }),
            "source": "multi_agent.log",
            "quality_score": quality,
            "ts": entry.get("ts", ""),
        }
        entries.append(dataset_entry)

    entries = entries[-MAX_ENTRIES_PER_DATASET:]

    avg_quality = (
        sum(e["quality_score"] for e in entries) / len(entries) if entries else 0.0
    )

    result = {
        "dataset_type": "reasoning",
        "entries": entries,
        "count": len(entries),
        "avg_quality": round(avg_quality, 4),
        "built_at": _now().isoformat(),
    }

    _save_json(DATASETS_DIR / "reasoning_dataset.json", result)

    _log({
        "type": "dataset_built",
        "dataset": "reasoning",
        "entries": len(entries),
        "avg_quality": round(avg_quality, 4),
    })

    return result


def build_metrics_dataset() -> dict:
    """Build a dataset from operational metrics.

    Each entry: {input, expected_output, source, quality_score}
    Input = query metrics, Output = outcome summary.

    Returns: {entries: list, count: int, avg_quality: float}
    """
    metrics = _load_metrics(days=14)

    entries = []
    for m in metrics:
        if m.get("type") != "query":
            continue

        quality = 0.5
        if "confidence" in m and "latency_ms" in m:
            quality = 0.7
        if m.get("escalated"):
            quality += 0.1  # Escalation data is valuable

        if quality < MIN_QUALITY_SCORE:
            continue

        dataset_entry = {
            "input": json.dumps({
                "query_type": m.get("query_type", ""),
                "pipeline": m.get("pipeline", ""),
                "model": m.get("model", ""),
            }),
            "expected_output": json.dumps({
                "confidence": m.get("confidence", 0),
                "latency_ms": m.get("latency_ms", 0),
                "escalated": m.get("escalated", False),
                "actions_suggested": m.get("actions_suggested", 0),
            }),
            "source": "metrics",
            "quality_score": round(quality, 4),
            "ts": m.get("ts", ""),
        }
        entries.append(dataset_entry)

    entries = entries[-MAX_ENTRIES_PER_DATASET:]

    avg_quality = (
        sum(e["quality_score"] for e in entries) / len(entries) if entries else 0.0
    )

    result = {
        "dataset_type": "metrics",
        "entries": entries,
        "count": len(entries),
        "avg_quality": round(avg_quality, 4),
        "built_at": _now().isoformat(),
    }

    _save_json(DATASETS_DIR / "metrics_dataset.json", result)

    _log({
        "type": "dataset_built",
        "dataset": "metrics",
        "entries": len(entries),
        "avg_quality": round(avg_quality, 4),
    })

    return result


# ── Main API ────────────────────────────────────────────────────


def build_all_datasets() -> dict:
    """Build all dataset types.

    Returns summary of all datasets built.
    """
    results = {
        "diagnostic": build_diagnostic_dataset(),
        "reasoning": build_reasoning_dataset(),
        "metrics": build_metrics_dataset(),
    }

    total = sum(r["count"] for r in results.values())

    _log({
        "type": "build_all",
        "total_entries": total,
        "datasets": {k: v["count"] for k, v in results.items()},
    })

    return {
        "datasets": {k: {"count": v["count"], "avg_quality": v["avg_quality"]}
                     for k, v in results.items()},
        "total_entries": total,
        "built_at": _now().isoformat(),
    }


def list_datasets() -> list:
    """List available datasets in the datasets/ directory."""
    if not DATASETS_DIR.exists():
        return []

    datasets = []
    for path in sorted(DATASETS_DIR.glob("*_dataset.json")):
        try:
            data = _load_json(path, {})
            datasets.append({
                "name": path.stem,
                "type": data.get("dataset_type", "unknown"),
                "count": data.get("count", 0),
                "avg_quality": data.get("avg_quality", 0),
                "built_at": data.get("built_at", ""),
            })
        except Exception:
            continue

    return datasets


# ── Tool Augmentation ──────────────────────────────────────────
# Generates synthetic training data for low-F1 tools in ChatML format.
# Uses Ollama for paraphrasing with fallback to rule-based variations.

import argparse
import hashlib
import random
import re
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

OLLAMA_URL = "http://192.168.2.6:11434"
# Resolve evals dir: works both on host (../../state/workspace/evals)
# and inside container (/home/tenazas/.openclaw/workspace/evals)
_host_evals = Path(__file__).parent.parent.parent / "state" / "workspace" / "evals"
_container_evals = Path("/home/tenazas/.openclaw/workspace/evals")
EVALS_DIR = _container_evals if _container_evals.parent.exists() else _host_evals

SYSTEM_MSG = (
    "Sos Tenazas, asistente personal de Sebastian. "
    "Respondé siempre con JSON válido indicando el tool a ejecutar."
)

# Seed examples per tool (español rioplatense)
TOOL_SEEDS: dict[str, list[str]] = {
    "smart-home-cinema": [
        "modo cine", "poneme cine", "activá el cine",
        "quiero ver peli", "prepará netflix", "modo película",
        "poné modo cine", "activá cinema", "vamos a ver una serie",
        "poné hbo", "disney plus", "quiero ver una movie",
    ],
    "smart-home-on": [
        "prendé las luces", "encendé luz", "apagá todo",
        "bajá las luces", "subí el brillo", "prendé la lámpara",
        "apagá las luces", "encendé todas", "luces on",
        "luces off", "subí brillo", "bajá el brillo",
    ],
    "self-evolve": [
        "análisis semanal", "cómo estás funcionando",
        "qué podés mejorar", "hacé el review",
        "mostrame las propuestas", "qué mejoras hay",
        "evolución del sistema", "self evolve",
        "revisá cómo venís", "analizate",
    ],
    "calendar": [
        "qué tengo mañana", "mis eventos de hoy",
        "agregá reunión", "libre el viernes",
        "agenda de la semana", "próximos eventos",
        "tengo algo a las 3", "qué hay en el calendario",
    ],
    "email": [
        "leé mis mails", "hay algo urgente",
        "resumí el inbox", "mandá mail a",
        "mis correos", "qué emails tengo",
        "chequeá el mail", "hay mails nuevos",
    ],
    "weather": [
        "cómo está el clima", "va a llover",
        "temperatura hoy", "pronóstico semana",
        "hace frío afuera", "qué onda el tiempo",
        "llueve hoy", "clima mañana",
    ],
    "infra-monitor": [
        "cómo está el nas", "containers caídos",
        "espacio en disco", "estado del sistema",
        "hay algo roto", "cómo andan los containers",
        "estado de docker", "salud de la infra",
    ],
    "arr-monitor": [
        "descargas activas", "qué está bajando",
        "estado sonarr", "cola de radarr",
        "hay algo descargando", "cómo va el torrent",
        "qué baja sonarr", "estado de lidarr",
    ],
}

# Fillers for rule-based variation
_FILLERS = ["por favor", "che", "dale", "ey", "mirá"]
_SOFTENERS = ["podés", "podrías", "me", ""]


def _ollama_generate(prompt: str, model: str = "qwen3:8b",
                     timeout: int = 30) -> str | None:
    """Call Ollama generate endpoint. Returns text or None on failure."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.9, "num_predict": 512},
    }).encode()
    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data.get("response", "").strip()
    except (URLError, OSError, json.JSONDecodeError, Exception):
        return None


def _generate_ollama_variations(seed: str, n: int = 3) -> list[str]:
    """Generate N variations of a seed phrase via Ollama."""
    prompt = (
        f"Generá {n} variaciones de esta frase en español rioplatense, "
        f"misma intención, distinto vocabulario. Solo las frases, una por "
        f"línea, sin explicaciones ni numeración: '{seed}'"
    )
    text = _ollama_generate(prompt)
    if not text:
        return []
    lines = [
        re.sub(r"^\d+[\.\)\-]\s*", "", line).strip().strip('"').strip("'")
        for line in text.splitlines()
        if line.strip() and len(line.strip()) > 3
    ]
    # Filter: not too similar to seed, not too long, must be Spanish
    return [
        line for line in lines[:n]
        if line.lower() != seed.lower()
        and len(line) < 120
        and _is_spanish(line)
    ]


def _is_spanish(text: str) -> bool:
    """Reject obvious non-Spanish variations (Portuguese, etc)."""
    t = text.lower()
    # Portuguese markers that don't appear in Spanish
    _pt_markers = [
        "você", "voce", "não", "nao ", "também", "tambem",
        "está ", "isso", "aqui", "fazer", "muito", "agora",
        "hoje", "amanhã", "manhã", "obrigad", "bom dia",
        "boa noite", "tudo bem", "lá ", " lá", "então",
        "entao", "chegad", "falar", "olhar", "ficar",
        "baixar ", "botar ", "colocar ",
    ]
    for marker in _pt_markers:
        if marker in t:
            return False
    # Reject if too many non-Spanish characters/patterns
    if re.search(r"[ãõç]", t):
        return False
    return True


def _generate_synthetic_variations(seed: str, n: int = 3) -> list[str]:
    """Rule-based fallback variations when Ollama is unavailable."""
    rng = random.Random(hashlib.md5(seed.encode()).hexdigest())
    variations = []
    words = seed.split()

    for _ in range(n):
        variant = list(words)
        # Random filler insertion
        if rng.random() < 0.5 and len(variant) > 1:
            filler = rng.choice(_FILLERS)
            pos = rng.choice([0, len(variant)])
            if pos == 0:
                variant.insert(0, filler)
            else:
                variant.append(filler)

        # Random softener prefix
        if rng.random() < 0.3:
            soft = rng.choice(_SOFTENERS)
            if soft:
                variant.insert(0, soft)

        # Random case variation
        if rng.random() < 0.3:
            variant = [w.upper() if rng.random() < 0.3 else w for w in variant]

        result = " ".join(variant).strip()
        if result.lower() != seed.lower() and result not in variations:
            variations.append(result)

    return variations[:n]


def _make_chatml_entry(query: str, tool: str, seed: str) -> dict:
    """Create a single ChatML training entry."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": query},
            {"role": "assistant", "content": json.dumps(
                {"tool": tool, "confidence": 0.95}, ensure_ascii=False,
            )},
        ],
        "metadata": {"tool": tool, "source": "augmented", "seed": seed},
    }


def _load_seed_file(path: str) -> dict[str, list[str]]:
    """Load seed examples from a JSONL file. Each line: {tool, query}."""
    seeds: dict[str, list[str]] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                tool = entry.get("tool", "")
                query = entry.get("query", "")
                if tool and query:
                    seeds.setdefault(tool, []).append(query)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return seeds


def augment_tools(
    tools: list[str] | None = None,
    samples: int = 100,
    variations: int = 3,
    output: str | None = None,
    seed_file: str | None = None,
) -> dict:
    """Generate augmented training data for specified tools.

    Args:
        tools: Tool names to augment (default: all in TOOL_SEEDS)
        samples: Target examples per tool
        variations: Variations per seed phrase
        output: Output JSONL path
        seed_file: Optional JSONL with extra seed examples

    Returns:
        Stats dict with counts per tool and output path.
    """
    if tools is None:
        tools = list(TOOL_SEEDS.keys())

    output_path = Path(output) if output else EVALS_DIR / "dataset_augmented.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Merge hardcoded seeds with seed file
    all_seeds = {t: list(TOOL_SEEDS.get(t, [])) for t in tools}
    if seed_file:
        extra = _load_seed_file(seed_file)
        for t in tools:
            if t in extra:
                all_seeds[t].extend(extra[t])
    else:
        # Auto-discover seed files in evals dir
        for candidate in EVALS_DIR.glob("*.jsonl"):
            if "augmented" not in candidate.name:
                extra = _load_seed_file(str(candidate))
                for t in tools:
                    if t in extra:
                        all_seeds[t].extend(extra[t])

    stats: dict[str, dict] = {}
    all_entries: list[dict] = []
    seen_queries: set[str] = set()

    for tool in tools:
        seeds = all_seeds.get(tool, [])
        if not seeds:
            stats[tool] = {"total": 0, "ollama": 0, "synthetic": 0,
                           "skipped": "no seeds"}
            continue

        tool_entries: list[dict] = []
        ollama_count = 0
        synthetic_count = 0

        # Add seed examples first
        for seed in seeds:
            key = f"{tool}:{seed.lower().strip()}"
            if key not in seen_queries:
                seen_queries.add(key)
                tool_entries.append(_make_chatml_entry(seed, tool, seed))

        # Generate variations until we hit target
        seed_idx = 0
        while len(tool_entries) < samples and seeds:
            seed = seeds[seed_idx % len(seeds)]
            seed_idx += 1

            # Try Ollama first
            ollama_vars = _generate_ollama_variations(seed, variations)
            if ollama_vars:
                for v in ollama_vars:
                    key = f"{tool}:{v.lower().strip()}"
                    if key not in seen_queries:
                        seen_queries.add(key)
                        tool_entries.append(_make_chatml_entry(v, tool, seed))
                        ollama_count += 1
                    if len(tool_entries) >= samples:
                        break
            else:
                # Fallback to synthetic
                syn_vars = _generate_synthetic_variations(seed, variations)
                for v in syn_vars:
                    key = f"{tool}:{v.lower().strip()}"
                    if key not in seen_queries:
                        seen_queries.add(key)
                        tool_entries.append(_make_chatml_entry(v, tool, seed))
                        synthetic_count += 1
                    if len(tool_entries) >= samples:
                        break

            # Safety: avoid infinite loop if seeds are exhausted
            if seed_idx > len(seeds) * (variations + 2) * 3:
                break

        stats[tool] = {
            "total": len(tool_entries),
            "ollama": ollama_count,
            "synthetic": synthetic_count,
            "seeds": len(seeds),
        }
        all_entries.extend(tool_entries)

    # Shuffle and write JSONL
    random.shuffle(all_entries)
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in all_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    total = len(all_entries)
    total_ollama = sum(s.get("ollama", 0) for s in stats.values())
    total_synthetic = sum(s.get("synthetic", 0) for s in stats.values())

    _log({
        "type": "tool_augmentation",
        "tools": tools,
        "total": total,
        "ollama": total_ollama,
        "synthetic": total_synthetic,
        "output": str(output_path),
    })

    return {
        "total": total,
        "ollama": total_ollama,
        "synthetic": total_synthetic,
        "per_tool": stats,
        "output": str(output_path),
    }


def _print_augment_stats(result: dict):
    """Pretty-print augmentation stats."""
    print(f"\n{'='*60}")
    print(f" Dataset Augmentation — {result['total']} ejemplos generados")
    print(f"{'='*60}")
    print(f" {'Tool':<22} {'Total':>6} {'Ollama':>8} {'Synth':>8} {'Seeds':>6}")
    print(f" {'-'*22} {'-'*6} {'-'*8} {'-'*8} {'-'*6}")
    for tool, s in result["per_tool"].items():
        if isinstance(s, dict) and "total" in s:
            print(f" {tool:<22} {s['total']:>6} {s.get('ollama',0):>8} "
                  f"{s.get('synthetic',0):>8} {s.get('seeds',0):>6}")
        else:
            print(f" {tool:<22} {'skip':>6}   {s.get('skipped','?')}")
    print(f" {'-'*22} {'-'*6} {'-'*8} {'-'*8}")
    print(f" {'TOTAL':<22} {result['total']:>6} {result['ollama']:>8} "
          f"{result['synthetic']:>8}")
    print(f"\n Output: {result['output']}")
    print(f"\n Copiar al HomeLab:")
    print(f"   scp {result['output']} sbasadre@192.168.2.6:~/tenazas_finetune/datasets/")
    print()


# ── CLI ────────────────────────────────────────────────────────


def _normalize_tool_name(name: str) -> str:
    """Normalize tool names: underscores → hyphens for consistency."""
    return name.replace("_", "-")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dataset Builder — operational datasets + tool augmentation",
    )
    sub = parser.add_subparsers(dest="command")

    # Legacy: build operational datasets
    sub.add_parser("build-all", help="Build all operational datasets")
    sub.add_parser("list", help="List available datasets")

    # Augmentation (also default when --tools is passed)
    aug = sub.add_parser("augment", help="Generate augmented tool training data")
    aug.add_argument("--tools", nargs="+", default=None,
                     help="Tool names to augment (default: all)")
    aug.add_argument("--samples", type=int, default=100,
                     help="Target examples per tool (default: 100)")
    aug.add_argument("--variations", type=int, default=3,
                     help="Variations per seed phrase (default: 3)")
    aug.add_argument("--output", default=None,
                     help="Output JSONL path")
    aug.add_argument("--seed-file", default=None,
                     help="Extra seed examples JSONL (tool + query per line)")

    # Also accept augment args at top level (default to augment)
    parser.add_argument("--tools", nargs="+", default=None,
                        help="Tool names to augment (default: all)")
    parser.add_argument("--samples", type=int, default=100,
                        help="Target examples per tool (default: 100)")
    parser.add_argument("--variations", type=int, default=3,
                        help="Variations per seed phrase (default: 3)")
    parser.add_argument("--output", default=None,
                        help="Output JSONL path")
    parser.add_argument("--seed-file", default=None,
                        help="Extra seed examples JSONL (tool + query per line)")

    args = parser.parse_args()

    # Default to augment when --tools is passed without subcommand
    if args.command is None and args.tools:
        args.command = "augment"

    if args.command == "build-all":
        result = build_all_datasets()
        print(json.dumps(result, indent=2))
    elif args.command == "list":
        for ds in list_datasets():
            print(f"  {ds['name']}: {ds['count']} entries "
                  f"(quality {ds['avg_quality']:.2f})")
    elif args.command == "augment":
        tools = [_normalize_tool_name(t) for t in args.tools] if args.tools else None
        result = augment_tools(
            tools=tools,
            samples=args.samples,
            variations=args.variations,
            output=args.output,
            seed_file=args.seed_file,
        )
        _print_augment_stats(result)
    else:
        parser.print_help()
