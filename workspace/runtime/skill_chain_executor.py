#!/usr/bin/env python3
"""
skill_chain_executor.py
Execute multiple skills in sequence or parallel with conditional logic.

Supports:
  - Parallel execution (run N skills simultaneously)
  - Conditional chains (if skill A fails → run B)
  - Summary merge (combine outputs)
  - Timeout per step
  - Fallback on failure
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path

RUNTIME = Path(__file__).parent
LOG_PATH = RUNTIME / "logs" / "skill_chain.log"


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _execute_skill(category: str, query: str) -> dict:
    """Execute a single skill via orchestrator."""
    sys.path.insert(0, str(RUNTIME))
    from orchestrator import execute_skill
    try:
        return execute_skill(category, query)
    except Exception as e:
        return {"skill": category, "success": False, "output": str(e), "error": "exception"}


# ── Chain definitions ────────────────────────────────────────────

def run_parallel(steps: list[dict], timeout: int = 30) -> list[dict]:
    """Run multiple skills in parallel.

    steps: [{"skill": "skill:weather", "query": "clima"}, ...]
    Returns: list of results in same order.
    """
    results = [None] * len(steps)

    def _run(idx, step):
        return idx, _execute_skill(step["skill"], step.get("query", ""))

    with ThreadPoolExecutor(max_workers=min(len(steps), 4)) as pool:
        futures = [pool.submit(_run, i, s) for i, s in enumerate(steps)]
        for f in futures:
            try:
                idx, result = f.result(timeout=timeout)
                results[idx] = result
            except FuturesTimeout:
                pass
            except Exception as e:
                pass

    # Fill None with error
    for i, r in enumerate(results):
        if r is None:
            results[i] = {"skill": steps[i]["skill"], "success": False,
                          "output": "timeout", "error": "timeout"}

    _log({"action": "parallel", "steps": len(steps),
          "success": sum(1 for r in results if r.get("success"))})
    return results


def run_chain(steps: list[dict], stop_on_fail: bool = True) -> list[dict]:
    """Run skills sequentially with optional stop on failure.

    steps: [{"skill": "...", "query": "...", "fallback": "skill:...", "timeout": 30}, ...]
    """
    results = []
    for step in steps:
        skill = step["skill"]
        query = step.get("query", "")
        timeout = step.get("timeout", 30)
        fallback = step.get("fallback")

        t0 = time.time()
        result = _execute_skill(skill, query)
        elapsed = time.time() - t0

        if not result.get("success") and fallback:
            _log({"action": "fallback", "from": skill, "to": fallback})
            result = _execute_skill(fallback, query)

        result["elapsed_ms"] = int(elapsed * 1000)
        results.append(result)

        if not result.get("success") and stop_on_fail:
            _log({"action": "chain_stopped", "at": skill, "step": len(results)})
            break

    _log({"action": "chain", "steps": len(steps), "completed": len(results),
          "success": sum(1 for r in results if r.get("success"))})
    return results


def run_conditional(steps: list[dict]) -> list[dict]:
    """Run skills with conditional logic.

    steps: [{"skill": "...", "query": "...", "condition": "prev_success"}, ...]
    condition: "prev_success", "prev_fail", "always" (default)
    """
    results = []
    prev_success = True

    for step in steps:
        condition = step.get("condition", "always")
        if condition == "prev_success" and not prev_success:
            continue
        if condition == "prev_fail" and prev_success:
            continue

        result = _execute_skill(step["skill"], step.get("query", ""))
        prev_success = result.get("success", False)
        results.append(result)

    return results


def merge_outputs(results: list[dict], separator: str = "\n\n") -> str:
    """Merge successful outputs into a single string."""
    parts = []
    for r in results:
        if r.get("success") and r.get("output"):
            parts.append(r["output"])
    return separator.join(parts) if parts else "Sin resultados."


# ── Prebuilt chains ──────────────────────────────────────────────

def morning_brief() -> str:
    """Parallel: calendar + weather + infra + finance."""
    results = run_parallel([
        {"skill": "skill:calendar-agenda", "query": "que tengo hoy"},
        {"skill": "skill:weather", "query": "clima"},
        {"skill": "skill:infra-monitor", "query": "estado"},
    ])
    return merge_outputs(results)


def full_diagnostic() -> str:
    """Chain: infra → network → arr-monitor."""
    results = run_chain([
        {"skill": "skill:infra-monitor", "query": "estado de containers"},
        {"skill": "skill:network-scanner", "query": "scan red"},
        {"skill": "skill:arr-monitor", "query": "descargas"},
    ], stop_on_fail=False)
    return merge_outputs(results)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--morning", action="store_true", help="Run morning brief")
    p.add_argument("--diagnostic", action="store_true", help="Run full diagnostic")
    args = p.parse_args()

    if args.morning:
        print(morning_brief())
    elif args.diagnostic:
        print(full_diagnostic())
    else:
        p.print_help()
