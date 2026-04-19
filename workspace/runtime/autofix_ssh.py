#!/usr/bin/env python3
"""
autofix_ssh.py — Whitelisted SSH commands to HOMELAB.

Only allows: "ollama pull <model>" where model is in pullable list.
"""

import subprocess
from pathlib import Path

HOMELAB_IP = "192.168.2.6"
HOMELAB_USER = "sbasadre"

_IN_CONTAINER = Path("/home/tenazas").exists()
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else Path(__file__).parent.parent / "state"
SSH_KEY = STATE_DIR / ".ssh" / "homelab_key"


def ssh_available() -> bool:
    """Check if SSH key exists."""
    return SSH_KEY.exists()


def ssh_setup_instructions() -> str:
    """Return instructions for setting up SSH key."""
    return (
        f"# Generate key:\n"
        f"ssh-keygen -t ed25519 -f {SSH_KEY} -N ''\n"
        f"# Copy pubkey to HOMELAB authorized_keys:\n"
        f"cat {SSH_KEY}.pub  # → add to {HOMELAB_USER}@{HOMELAB_IP}:.ssh/authorized_keys"
    )


def ollama_pull(model: str, pullable_models: set, timeout: int = 900) -> dict:
    """Pull an Ollama model on HOMELAB via SSH.

    Args:
        model: model name (e.g. "qwen2.5:7b")
        pullable_models: set of allowed model names
        timeout: seconds (default 15 min for large downloads)

    Returns:
        {success, action, output/error/reason}
    """
    if model not in pullable_models:
        return {"success": False, "action": "blocked",
                "reason": f"'{model}' not in pullable whitelist"}

    if not ssh_available():
        return {"success": False, "action": "needs_setup",
                "reason": "SSH key not configured",
                "setup": ssh_setup_instructions()}

    try:
        r = subprocess.run(
            ["ssh", "-i", str(SSH_KEY),
             "-o", "ConnectTimeout=5",
             "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=no",
             f"{HOMELAB_USER}@{HOMELAB_IP}",
             f"ollama pull {model}"],
            capture_output=True, text=True, timeout=timeout)
        return {"success": r.returncode == 0, "action": "pull_model",
                "output": r.stdout.strip()[-300:],
                "stderr": r.stderr.strip()[-200:] if r.returncode != 0 else ""}
    except subprocess.TimeoutExpired:
        return {"success": False, "action": "pull_model",
                "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"success": False, "action": "pull_model", "error": str(e)}
