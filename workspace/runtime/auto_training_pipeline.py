#!/usr/bin/env python3
"""
auto_training_pipeline.py
Pipeline diario de auto-retraining para el router de Tenazas.

Flujo:
  1. Correr eval_runner → obtener F1 actual
  2. Si F1 < threshold → detectar tools flojos
  3. apply_improvements → generar seeds nuevos
  4. Regenerar dataset mergeando con seeds nuevos
  5. Entrenar en Workbench via SSH
  6. Convertir GGUF + deploy en Ollama
  7. Eval del modelo nuevo
  8. Si F1_nuevo > F1_actual + MIN_IMPROVEMENT → swap
  9. Si no mejora → no swap + notifica para revision manual
  10. Notificar resultado por Telegram

Config en openclaw.json → tenazas.auto_training:
  enabled, f1_threshold, min_improvement, dataset_path, model_version
"""

import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

# ── Paths ────────────────────────────────────────────────────────

RUNTIME = Path(__file__).parent
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
EVALS_DIR = Path("/home/tenazas/.openclaw/workspace/evals")
ROUTER_PATH = RUNTIME / "finetuned_router.py"
SEEDS_PATH = EVALS_DIR / "auto_seeds.jsonl"
LOG_FILE = RUNTIME / "logs" / "auto_training.log"

# HomeLab
HOMELAB_HOST = "sbasadre@192.168.2.6"
HOMELAB_KEY = "/var/services/homes/sbasadre/.ssh/id_homelab"
WORKBENCH_CTR = "project-tenazas-router-workbench-clean"
OLLAMA_URL = "http://192.168.2.6:11434"
CHAT_ID = "1565794551"

log = logging.getLogger("auto_training")


# ── Config ───────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg.get("tenazas", {}).get("auto_training", {})
    except Exception:
        return {}


def _save_config_key(key: str, value):
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        cfg.setdefault("tenazas", {}).setdefault("auto_training", {})[key] = value
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    except Exception as e:
        log.warning("Could not save config key %s: %s", key, e)


# ── Telegram ─────────────────────────────────────────────────────

def _notify(msg: str):
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception as e:
        log.warning("Telegram failed: %s", e)


# ── SSH helpers ──────────────────────────────────────────────────

def _ssh(cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-i", HOMELAB_KEY,
             HOMELAB_HOST, cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def _scp(local: str, remote: str) -> bool:
    try:
        r = subprocess.run(
            ["scp", "-o", "StrictHostKeyChecking=no", "-i", HOMELAB_KEY,
             local, f"{HOMELAB_HOST}:{remote}"],
            capture_output=True, text=True, timeout=120,
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Step 1: Eval actual ─────────────────────────────────────────

def step_eval(dataset_path: str) -> dict:
    sys.path.insert(0, str(RUNTIME))
    try:
        from eval_runner import run_eval
        return run_eval(dataset_path)
    except Exception as e:
        return {"avg_f1": 0.0, "per_tool": {}, "error": str(e)}


# ── Step 2: Detect weak tools ───────────────────────────────────

def step_weak_tools(eval_result: dict, threshold: float) -> list:
    return sorted([
        {"tool": t, "f1": m.get("f1", 0)}
        for t, m in eval_result.get("per_tool", {}).items()
        if m.get("f1", 0) < threshold
    ], key=lambda x: x["f1"])


# ── Step 3: Generate seeds ───────────────────────────────────────

def step_generate_seeds(weak_tools: list) -> int:
    """Generate seeds for weak tools via dataset_builder. Returns count."""
    sys.path.insert(0, str(RUNTIME))
    try:
        from dataset_builder import augment_tools
        tools = [t["tool"] for t in weak_tools]
        result = augment_tools(tools=tools, samples=20, variations=2,
                               output=str(SEEDS_PATH))
        return result.get("total", 0)
    except Exception as e:
        log.error("generate_seeds failed: %s", e)
        return 0


# ── Step 4: Merge + balance + split ─────────────────────────────

def step_merge_dataset(base_path: str, version: str) -> tuple[str, str, str]:
    """Merge base + seeds, strip system, balance, split. Returns (full, train, val) paths."""
    full_path = str(EVALS_DIR / f"dataset_auto_{version}.jsonl")
    train_path = str(EVALS_DIR / f"train_auto_{version}.jsonl")
    val_path = str(EVALS_DIR / f"val_auto_{version}.jsonl")

    try:
        base_lines = Path(base_path).read_text().splitlines()
        seed_lines = SEEDS_PATH.read_text().splitlines() if SEEDS_PATH.exists() else []

        seen = set()
        merged = []
        for l in base_lines + seed_lines:
            if not l.strip():
                continue
            ex = json.loads(l)
            ex["messages"] = [m for m in ex["messages"] if m["role"] != "system"]
            user = [m for m in ex["messages"] if m["role"] == "user"]
            key = user[0]["content"].lower().strip() if user else ""
            if key and key not in seen:
                seen.add(key)
                merged.append(ex)

        # Balance: min 60 per tool
        by_tool = {}
        for ex in merged:
            t = ex.get("metadata", {}).get("tool", "?")
            by_tool.setdefault(t, []).append(ex)

        balanced = []
        for tool, examples in by_tool.items():
            if len(examples) < 60:
                balanced.extend(examples + random.choices(examples, k=60 - len(examples)))
            else:
                balanced.extend(examples)

        random.seed(42)
        random.shuffle(balanced)
        split = int(len(balanced) * 0.9)

        for path, data in [(full_path, balanced),
                           (train_path, balanced[:split]),
                           (val_path, balanced[split:])]:
            with open(path, "w") as f:
                for ex in data:
                    f.write(json.dumps(ex, ensure_ascii=False) + "\n")

        log.info("Dataset %s: %d total, %d train, %d val",
                 version, len(balanced), split, len(balanced) - split)
        return full_path, train_path, val_path
    except Exception as e:
        log.error("merge_dataset failed: %s", e)
        return "", "", ""


# ── Step 5: Train on Workbench ───────────────────────────────────

def step_train(train_path: str, val_path: str, version: str) -> bool:
    """Copy dataset to Workbench, run training."""
    model_name = f"tenazas-qwen25-7b-{version}"

    # SCP to HomeLab host
    if not _scp(train_path, f"C:/Users/sbasadre/train_{version}.jsonl"):
        return False
    if not _scp(val_path, f"C:/Users/sbasadre/val_{version}.jsonl"):
        return False

    # Docker cp into Workbench
    for split in ["train", "val"]:
        code, _, err = _ssh(
            f"docker cp C:\\Users\\sbasadre\\{split}_{version}.jsonl "
            f"{WORKBENCH_CTR}:/project/data/{split}.jsonl", timeout=60)
        if code != 0:
            log.error("docker cp failed: %s", err)
            return False

    # Run training (reuse existing script, override output dir)
    code, stdout, stderr = _ssh(
        f"docker exec {WORKBENCH_CTR} python3 /project/code/train_7b_v6.py",
        timeout=3600,
    )

    if code != 0 or "TRAINING COMPLETE" not in stdout:
        log.error("Training failed: %s", stderr[-500:] if stderr else "no output")
        return False

    log.info("Training %s complete", version)
    return True


# ── Step 6: GGUF + Deploy ───────────────────────────────────────

def step_deploy(version: str) -> bool:
    """Copy merged from Workbench, convert GGUF, create Ollama model."""
    model_name = f"tenazas-qwen25-7b-{version}"
    merged_dir = f"C:\\Users\\sbasadre\\tenazas_finetune\\{version}_merged"
    gguf_path = f"C:\\Users\\sbasadre\\tenazas_finetune\\{model_name}-Q8_0.gguf"

    # Copy merged from container
    _ssh(f"mkdir {merged_dir} 2>nul", timeout=10)
    code, _, err = _ssh(
        f"docker cp {WORKBENCH_CTR}:/project/models/{model_name}/merged/. {merged_dir}",
        timeout=300)
    if code != 0:
        log.error("Copy merged failed: %s", err)
        return False

    # Convert to GGUF
    code, _, err = _ssh(
        f"C:\\Python311\\python.exe C:\\llama.cpp\\convert_hf_to_gguf.py "
        f"{merged_dir} --outfile {gguf_path} --outtype q8_0",
        timeout=600)
    if code != 0:
        log.error("GGUF conversion failed: %s", err)
        return False

    # Create Modelfile + ollama create
    modelfile_path = f"C:\\Users\\sbasadre\\tenazas_finetune\\Modelfile_{version}"
    modelfile_content = (
        f"FROM {gguf_path}\n"
        "PARAMETER temperature 0.1\n"
        "PARAMETER num_predict 128\n"
        "TEMPLATE \"\"\"<|im_start|>system\n"
        "Sos un clasificador de intenciones para un asistente domotico. "
        "Dado un mensaje en espanol, responde UNICAMENTE con JSON valido: "
        "{\"tool\": \"<nombre>\", \"confidence\": 0.95}. "
        "Tools: smart-home-on, smart-home-off, smart-home-cinema, weather, "
        "email, calendar, arr-monitor, infra-monitor, self-evolve, tts, "
        "reminders, smoking-tracker, chat<|im_end|>\n"
        "<|im_start|>user\n{{ .Prompt }}<|im_end|>\n"
        "<|im_start|>assistant\n\"\"\"\n"
    )

    # Write Modelfile via echo (escape quotes for Windows)
    _scp_content(modelfile_content, modelfile_path)

    code, _, err = _ssh(
        f"cd C:\\Users\\sbasadre\\tenazas_finetune && ollama create {model_name} -f Modelfile_{version}",
        timeout=300)
    if code != 0:
        log.error("ollama create failed: %s", err)
        return False

    log.info("Deployed %s to Ollama", model_name)
    return True


def _scp_content(content: str, remote_path: str):
    """Write content to a temp file and SCP it."""
    tmp = Path("/tmp/auto_train_modelfile")
    tmp.write_text(content)
    _scp(str(tmp), remote_path)
    tmp.unlink(missing_ok=True)


# ── Step 7-9: Eval + Canary Swap ────────────────────────────────

def step_canary_swap(version: str, current_f1: float,
                     min_improvement: float, dataset_path: str) -> bool:
    """Eval new model, swap only if F1 improves >= min_improvement."""
    model_name = f"tenazas-qwen25-7b-{version}"

    # Save current router state for rollback
    original_content = ROUTER_PATH.read_text()

    try:
        # Temporarily swap model for eval
        new_content = original_content.replace(
            f'FINETUNED_MODEL = "',
            f'FINETUNED_MODEL = "{model_name}"\n# OLD: FINETUNED_MODEL = "',
            1,
        )
        # Cleaner: just replace the model name
        import re
        new_content = re.sub(
            r'FINETUNED_MODEL = "[^"]*"',
            f'FINETUNED_MODEL = "{model_name}"',
            original_content,
            count=1,
        )
        ROUTER_PATH.write_text(new_content)

        # Eval new model
        new_eval = step_eval(dataset_path)
        new_f1 = new_eval.get("avg_f1", 0.0)
        improvement = new_f1 - current_f1

        if improvement >= min_improvement:
            log.info("SWAP: %s (F1 %.1f%% → %.1f%%, +%.1f%%)",
                     model_name, current_f1 * 100, new_f1 * 100, improvement * 100)
            return True
        else:
            # Rollback
            ROUTER_PATH.write_text(original_content)
            log.info("NO SWAP: %s (F1 %.1f%% → %.1f%%, +%.1f%% < min %.1f%%)",
                     model_name, current_f1 * 100, new_f1 * 100,
                     improvement * 100, min_improvement * 100)
            return False
    except Exception as e:
        ROUTER_PATH.write_text(original_content)
        log.error("Canary swap failed, rolled back: %s", e)
        return False


# ── Main Pipeline ────────────────────────────────────────────────

def run_pipeline():
    cfg = _load_config()
    if not cfg.get("enabled", False):
        log.info("Auto-training disabled")
        return

    threshold = cfg.get("f1_threshold", 0.85)
    min_improvement = cfg.get("min_improvement", 0.02)
    dataset_path = cfg.get("dataset_path",
                           str(EVALS_DIR / "dataset_v6_balanced.jsonl"))
    current_version = cfg.get("model_version", "v6")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    log.info("=== Auto-training pipeline — %s ===", now)
    _notify(f"Auto-training iniciado — {now}")

    # Step 1: Eval
    eval_result = step_eval(dataset_path)
    current_f1 = eval_result.get("avg_f1", 0.0)
    log.info("Current F1: %.1f%%", current_f1 * 100)

    if current_f1 >= threshold:
        _notify(f"Router OK — F1 {current_f1:.1%} >= {threshold:.1%}. Sin retraining.")
        return

    # Step 2: Weak tools
    weak = step_weak_tools(eval_result, threshold)
    if not weak:
        _notify(f"F1 bajo ({current_f1:.1%}) pero sin tools identificados.")
        return

    weak_str = ", ".join(f"{t['tool']}({t['f1']:.0%})" for t in weak[:5])
    _notify(f"F1 bajo: {current_f1:.1%}\nTools flojos: {weak_str}\nRetraining...")

    # Step 3: Seeds
    seeds_count = step_generate_seeds(weak)
    log.info("Generated %d seeds", seeds_count)

    # Step 4: Dataset
    new_version = _next_version(current_version)
    full, train, val = step_merge_dataset(dataset_path, new_version)
    if not full:
        _notify("Error regenerando dataset. Abortado.")
        return

    # Step 5: Train
    _notify(f"Entrenando {new_version} en HomeLab...")
    if not step_train(train, val, new_version):
        _notify(f"Training {new_version} fallo. Abortado.")
        return

    # Step 6: Deploy
    if not step_deploy(new_version):
        _notify(f"Deploy {new_version} fallo. Abortado.")
        return

    # Step 7-9: Canary swap
    swapped = step_canary_swap(new_version, current_f1, min_improvement, full)

    if swapped:
        _save_config_key("model_version", new_version)
        _notify(
            f"Router actualizado: {new_version}\n"
            f"F1: {current_f1:.1%} → mejorado\n"
            f"Tools: {weak_str}"
        )
    else:
        _notify(
            f"{new_version} no supera minimo de mejora.\n"
            f"Modelo actual mantenido. Revision manual."
        )

    log.info("=== Pipeline complete ===")


def _next_version(current: str) -> str:
    try:
        n = int(current.lstrip("v"))
        return f"v{n + 1}"
    except Exception:
        return f"{current}_next"


if __name__ == "__main__":
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE, mode="a"),
        ],
    )
    run_pipeline()
