#!/usr/bin/env python3
"""
autofix_aider.py — Aider CLI wrapper for autofix Tier 2.

Invokes Aider as subprocess with constrained system prompt.
Reads model and path config from autofix_config.json.
Detects UNFIXABLE responses.
"""

import json
import os
import re
import subprocess
from pathlib import Path

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

_IN_CONTAINER = Path("/home/tenazas").exists()
REPO_DIR = Path("/home/tenazas") if _IN_CONTAINER else Path(__file__).parent.parent
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else BASE_DIR.parent / "state"
CONFIG_PATH = STATE_DIR / "autofix_config.json"

# Defaults (overridden by config llm_backends section)
_DEFAULT_OLLAMA_URL = OLLAMA_GPU_URL
_DEFAULT_AIDER_PATH = "/home/tenazas/.openclaw/aider-venv/bin/aider"

SYSTEM_PROMPT = """\
Sos el auto-fix agent de Tenazas. Reglas ABSOLUTAS:

1. Arreglá SOLO el bug específico reportado. Mínimo cambio.
2. NO refactorices. NO optimices. NO agregues features.
3. NO toqués archivos fuera del stacktrace proporcionado.
4. NO asumas cuál es el fix antes de LEER el código existente.
5. Si hay múltiples fixes posibles, elegí el menos invasivo.
6. Si no podés arreglarlo con ≤10 líneas o tocando >2 archivos,
   respondé EXACTAMENTE "UNFIXABLE: <razón breve>".
7. NO toqués: safety_guard.py, docker_wrapper.py, orchestrator.py,
   runtime.py, executors.py, infra_diagnostics.py, state/openclaw.json,
   credentials/. Si el fix requiere tocar estos, respondé UNFIXABLE.
8. Preservá el estilo del código existente (naming, comments en español,
   stdlib only, sin frameworks).
9. Si el bug sugiere que una feature nunca existió (vs regresión),
   respondé "UNFIXABLE: feature missing, needs human design".
"""


def _load_llm_config() -> dict:
    """Load llm_backends section from config."""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("llm_backends", {})
    except Exception:
        pass
    return {}


def _get_aider_path() -> str:
    """Get aider binary path from config or default."""
    cfg = _load_llm_config()
    path = cfg.get("aider_path", _DEFAULT_AIDER_PATH)
    # Fall back to bare "aider" if configured path doesn't exist
    if not Path(path).exists():
        return "aider"
    return path


def is_installed() -> bool:
    """Check if aider-chat is installed."""
    aider = _get_aider_path()
    try:
        r = subprocess.run([aider, "--version"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        # Try pip check as fallback
        try:
            r = subprocess.run(["pip", "show", "aider-chat"],
                               capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False


def collect_context(probe: str, detail: dict) -> dict:
    """Collect error context for Aider prompt.

    Returns:
        {probe, stacktrace, log_tail, files, detail}
    """
    ctx = {
        "probe": probe,
        "detail": detail,
        "stacktrace": "",
        "log_tail": "",
        "files": [],
    }

    # Get error message
    msg = detail.get("message", "")
    if "Traceback" in msg or "Error" in msg:
        ctx["stacktrace"] = msg[:2000]

    # Search logs for relevant errors
    log_dir = BASE_DIR / "logs"
    log_candidates = [
        log_dir / f"{probe}.log",
        log_dir / "runtime.log",
        log_dir / "orchestrator.log",
        log_dir / "metrics_server.log",
    ]
    for lf in log_candidates:
        if lf.exists():
            try:
                lines = lf.read_text(errors="replace").strip().split("\n")
                error_lines = [
                    l for l in lines[-100:]
                    if any(k in l.lower() for k in
                           ["error", "exception", "traceback", "fail", "crash"])
                ]
                if error_lines:
                    ctx["log_tail"] = "\n".join(error_lines[-30:])
                    break
            except Exception:
                pass

    # Extract files from stacktrace
    file_re = re.compile(r'File "([^"]+\.py)"')
    text = ctx["stacktrace"] + "\n" + ctx["log_tail"]
    for m in file_re.finditer(text):
        fpath = m.group(1)
        if "/tenazas/" in fpath or "runtime/" in fpath:
            rel = fpath.split("/tenazas/")[-1] if "/tenazas/" in fpath else fpath
            if rel not in ctx["files"]:
                ctx["files"].append(rel)

    return ctx


def build_prompt(probe: str, detail: dict, context: dict) -> str:
    """Build the full prompt for Aider."""
    error_summary = detail.get("message", probe)
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"--- BUG REPORT ---\n"
        f"Probe: {probe}\n"
        f"Error: {error_summary}\n"
        f"Stacktrace:\n{context['stacktrace'][:1500]}\n\n"
        f"Log relevante (últimas líneas con errores):\n"
        f"{context['log_tail'][:1000]}\n\n"
        f"Tu trabajo: arreglar SOLO este bug con el mínimo cambio posible."
    )


def run_aider(prompt: str, files: list[str], model: str,
              backend: str, timeout: int = 180) -> dict:
    """Invoke Aider CLI.

    Args:
        prompt: full prompt text
        files: list of file paths to edit
        model: model identifier (e.g. "qwen2.5-coder:14b")
        backend: "ollama" or "gemini"
        timeout: seconds

    Returns:
        {success, output, unfixable, unfixable_reason, error}
    """
    llm_cfg = _load_llm_config()
    ollama_url = llm_cfg.get("ollama_base_url", _DEFAULT_OLLAMA_URL)
    aider = _get_aider_path()

    # Build model flag
    if backend == "ollama":
        model_flag = f"ollama_chat/{model}" if "/" not in model else model
        env = {**os.environ, "OLLAMA_API_BASE": ollama_url}
    else:
        model_flag = model  # e.g. "gemini/gemini-2.5-flash"
        env = dict(os.environ)

    cmd = [
        aider,
        "--model", model_flag,
        "--no-auto-commits",
        "--yes",
        "--map-tokens", "0",
        "--no-stream",
        "--message", prompt,
    ] + files

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=str(REPO_DIR), env=env)
        output = r.stdout + r.stderr

        # Check for UNFIXABLE
        unfixable = False
        unfixable_reason = ""
        for line in output.split("\n"):
            if "UNFIXABLE" in line:
                unfixable = True
                unfixable_reason = line.split("UNFIXABLE:")[-1].strip() if ":" in line else line
                break

        return {
            "success": r.returncode == 0 and not unfixable,
            "output": output[-2000:],
            "unfixable": unfixable,
            "unfixable_reason": unfixable_reason,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "unfixable": False,
                "unfixable_reason": "", "error": f"timeout after {timeout}s"}
    except FileNotFoundError:
        return {"success": False, "output": "", "unfixable": False,
                "unfixable_reason": "", "error": "aider not installed"}
    except Exception as e:
        return {"success": False, "output": "", "unfixable": False,
                "unfixable_reason": "", "error": str(e)}
