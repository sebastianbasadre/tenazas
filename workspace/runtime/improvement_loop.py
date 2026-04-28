#!/usr/bin/env python3
"""
Improvement Loop — Closed-loop: approved candidate → experiment → validate → staging.

For each approved candidate:
1. Creates experiment with 10% traffic
2. Polls results (configurable interval)
3. If improvement >= threshold → mark validated, stage
4. If no improvement → mark failed, log to decision_quality
5. Notify Sebas with concrete metric result

NEVER promotes to runtime direct — always staging + final approval.

Usage:
    python3 improvement_loop.py --run       # process all approved candidates
    python3 improvement_loop.py --status    # show loop status
"""

import json
import time
from datetime import datetime
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
CANDIDATES_FILE = STATE_DIR / "improvement_candidates.json"
LOOP_STATE_FILE = STATE_DIR / "improvement_loop.json"
LOG_FILE = BASE_DIR / "logs" / "improvement_loop.log"

IMPROVEMENT_THRESHOLD = 0.02  # 2% improvement required
POLL_INTERVAL_SECONDS = 1800  # 30 min
MAX_WAIT_HOURS = 48
MIN_SAMPLES = 10



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _notify(msg: str):
    try:
        config_path = Path("/home/tenazas/.openclaw/openclaw.json")
        if not config_path.exists():
            return
        token = json.loads(config_path.read_text())["channels"]["telegram"]["botToken"]
        from urllib.request import Request, urlopen
        payload = json.dumps({
            "chat_id": get_default_telegram_chat_id(),
            "text": msg[:4090],
        }).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass


# ── Core API ────────────────────────────────────────────────────


def get_approved_candidates() -> list:
    """Get candidates with status approved that haven't been processed."""
    candidates = load_json(CANDIDATES_FILE, [])
    if not isinstance(candidates, list):
        return []
    loop_state = load_json(LOOP_STATE_FILE, {"processed": []})
    processed_ids = set(loop_state.get("processed", []))
    return [c for c in candidates
            if c.get("status") == "approved" and c.get("id") not in processed_ids]


def create_experiment_for(candidate: dict) -> dict | None:
    """Create an experiment for an approved candidate."""
    try:
        import experiment_runner as er
        exp = er.create(
            name=f"imp_{candidate['id'][:8]}_{candidate['type']}",
            exp_type="prompt_ab",
            config={"candidate_id": candidate["id"],
                    "candidate_type": candidate["type"],
                    "title": candidate.get("title", "")},
            traffic_percent=10,
        )
        if "error" in exp:
            _log({"action": "experiment_create_error", "candidate": candidate["id"],
                  "error": exp["error"]})
            return None
        _log({"action": "experiment_created", "candidate": candidate["id"],
              "experiment": exp["id"]})
        return exp
    except Exception as e:
        _log({"action": "experiment_create_error", "candidate": candidate["id"],
              "error": str(e)[:200]})
        return None


def evaluate_experiment(experiment_id: str) -> dict:
    """Evaluate experiment results. Returns {improved, metric_delta, details}."""
    try:
        import experiment_runner as er
        results = er.get_results(experiment_id)
        if not results:
            return {"improved": False, "reason": "no results", "metric_delta": 0}

        control_avg = results.get("control_avg", 0)
        variant_avg = results.get("variant_avg", 0)
        control_n = results.get("control_n", 0)
        variant_n = results.get("variant_n", 0)

        if control_n < MIN_SAMPLES or variant_n < 3:
            return {"improved": False, "reason": "insufficient samples",
                    "control_n": control_n, "variant_n": variant_n}

        delta = variant_avg - control_avg
        improved = delta >= IMPROVEMENT_THRESHOLD

        return {
            "improved": improved,
            "metric_delta": round(delta, 4),
            "control_avg": round(control_avg, 4),
            "variant_avg": round(variant_avg, 4),
            "control_n": control_n,
            "variant_n": variant_n,
            "reason": "improved" if improved else "no improvement",
        }
    except Exception as e:
        return {"improved": False, "reason": str(e)[:200], "metric_delta": 0}


def process_candidate(candidate: dict) -> dict:
    """Process a single approved candidate through the loop.

    Returns: {candidate_id, action, experiment_id, result}
    """
    cid = candidate["id"]

    # Create experiment
    exp = create_experiment_for(candidate)
    if not exp:
        return {"candidate_id": cid, "action": "skipped", "reason": "experiment creation failed"}

    # Evaluate (in non-blocking mode, just check current results)
    evaluation = evaluate_experiment(exp["id"])

    # Record result
    result = {
        "candidate_id": cid,
        "experiment_id": exp["id"],
        "evaluation": evaluation,
        "processed_at": now().isoformat(),
    }

    if evaluation["improved"]:
        result["action"] = "validated"
        # Mark candidate as validated → staging
        try:
            import improvement_candidates as ic
            # Update with validated flag
            candidates = load_json(CANDIDATES_FILE, [])
            for c in candidates:
                if c.get("id") == cid:
                    c["status"] = "approved"  # keep approved, add validated flag
                    c["validated"] = True
                    c["validated_at"] = now().isoformat()
                    c["experiment_results"] = evaluation
            save_json(CANDIDATES_FILE, candidates)
        except Exception:
            pass

        _notify(f"Mejora validada: [{candidate['type']}] {candidate.get('title', '')[:50]}\n"
                f"Delta: {evaluation['metric_delta']:+.4f} "
                f"(control: {evaluation.get('control_avg', 0):.4f}, "
                f"variant: {evaluation.get('variant_avg', 0):.4f})\n"
                f"Queda en staging para aprobacion final.")
    else:
        result["action"] = "failed"
        # Record as bad decision
        try:
            import decision_quality as dq
            dq.score(action="improvement_experiment", target=cid,
                     validation_result={"success": False,
                                        "reason": evaluation.get("reason", "no improvement")},
                     confidence=0.5, context=f"candidate {candidate['type']}: {candidate.get('title', '')[:100]}")
        except Exception:
            pass

        _notify(f"Mejora NO mejoró: [{candidate['type']}] {candidate.get('title', '')[:50]}\n"
                f"Razón: {evaluation.get('reason', '?')}")

    # Mark as processed
    loop_state = load_json(LOOP_STATE_FILE, {"processed": []})
    loop_state["processed"].append(cid)
    loop_state["processed"] = loop_state["processed"][-100:]  # cap
    loop_state["last_run"] = now().isoformat()
    save_json(LOOP_STATE_FILE, loop_state)

    _log({"action": "candidate_processed", **result})
    return result


def run() -> list:
    """Process all approved candidates. Returns list of results."""
    candidates = get_approved_candidates()
    if not candidates:
        _log({"action": "run", "candidates": 0})
        return []

    results = []
    for c in candidates:
        result = process_candidate(c)
        results.append(result)

    _log({"action": "run_complete", "processed": len(results)})
    return results


def get_status() -> dict:
    """Get loop status."""
    loop_state = load_json(LOOP_STATE_FILE, {"processed": []})
    pending = get_approved_candidates()
    return {
        "pending": len(pending),
        "processed_total": len(loop_state.get("processed", [])),
        "last_run": loop_state.get("last_run"),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Improvement Loop")
    p.add_argument("--run", action="store_true", help="Process approved candidates")
    p.add_argument("--status", action="store_true", help="Show loop status")
    args = p.parse_args()

    if args.run:
        results = run()
        print(f"Processed {len(results)} candidates")
        for r in results:
            print(f"  {r['candidate_id']}: {r['action']}")
    elif args.status:
        print(json.dumps(get_status(), indent=2))
    else:
        p.print_help()
