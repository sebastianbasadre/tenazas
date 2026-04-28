#!/usr/bin/env python3
"""
Fine-Tune Pipeline — End-to-end fine-tuning orchestration.

4 phases:
  1. Prepare: validate dataset, split train/eval, SCP to HomeLab
  2. Fine-tune: run Unsloth LoRA training on HomeLab via SSH
  3. Deploy: create Ollama model from GGUF, verify
  4. Eval: run eval_runner, compare F1, rollback if worse

Each phase sends Telegram notifications and waits for confirmation
(except Phase 4 eval which runs automatically).

Safety: Never auto-executes without approval (unless --auto-approve).
        Never modifies production router without eval pass.

Log: workspace/runtime/logs/fine_tune_pipeline.log
"""

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

BASE_DIR = Path(__file__).parent
_host_evals = BASE_DIR.parent.parent / "state" / "workspace" / "evals"
_container_evals = Path("/home/tenazas/.openclaw/workspace/evals")
EVALS_DIR = _container_evals if _container_evals.parent.exists() else _host_evals
LOG_FILE = BASE_DIR / "logs" / "fine_tune_pipeline.log"
DEFAULT_DATASET = EVALS_DIR / "dataset_augmented.jsonl"

# HomeLab config
HOMELAB_HOST = "sbasadre@192.168.2.6"
HOMELAB_DIR = "~/tenazas_finetune"
OLLAMA_GPU_URL = "http://192.168.2.6:11434"

# Training defaults
TRAIN_CONFIG = {
    "base_model": "unsloth/Qwen2.5-7B-Instruct",
    "lora_rank": 16,
    "lora_alpha": 32,
    "target_modules": ["q_proj", "v_proj", "k_proj", "o_proj"],
    "batch_size": 4,
    "gradient_accumulation": 4,
    "learning_rate": 2e-4,
    "epochs": 8,
    "max_seq_length": 2048,
    "output_name": "tenazas-qwen25-7b-v3",
    "quantization": "q8_0",
}

# Telegram config
CHAT_ID = get_default_telegram_chat_id()



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_telegram_token() -> str | None:
    """Load Telegram bot token from openclaw config."""
    config_path = Path("/home/tenazas/.openclaw/openclaw.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("channels", {}).get("telegram", {}).get("botToken")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _notify(msg: str):
    """Send Telegram notification."""
    token = _load_telegram_token()
    if not token:
        print(f"[telegram] (no token) {msg}")
        return
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": msg[:4090],
    }).encode()
    try:
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urlopen(req, timeout=10)
    except (URLError, OSError) as e:
        print(f"[telegram] Error: {e}")


def _confirm(phase: str, details: str, auto: bool = False,
             dry_run: bool = False) -> bool:
    """Ask for confirmation before proceeding. Returns True if approved."""
    if auto or dry_run:
        print(f"[{'dry-run' if dry_run else 'auto-approve'}] "
              f"Phase {phase}: proceeding")
        return True

    _notify(f"Fine-tune Phase {phase}\n{details}\n\nEsperando confirmación...")
    print(f"\n{'='*60}")
    print(f" Phase {phase}")
    print(f" {details}")
    print(f"{'='*60}")
    try:
        answer = input(" Continuar? [y/N] ").strip().lower()
    except EOFError:
        print(" (no TTY, auto-skipping)")
        return False
    return answer in ("y", "yes", "si", "sí")


def _ssh(cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    """Run command on HomeLab via SSH."""
    full_cmd = ["ssh", HOMELAB_HOST, cmd]
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "SSH command timed out"
    except Exception as e:
        return -1, "", str(e)


def _scp(local: str, remote: str) -> bool:
    """Copy file to HomeLab via SCP."""
    try:
        result = subprocess.run(
            ["scp", local, f"{HOMELAB_HOST}:{remote}"],
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Phase 1: Prepare ────────────────────────────────────────────


def phase_prepare(dataset_path: str, dry_run: bool = False) -> dict:
    """Validate dataset, split train/eval, copy to HomeLab.

    Returns: {success, train_count, eval_count, per_tool, paths}
    """
    _log({"phase": 1, "action": "start", "dataset": dataset_path})

    # Load and validate
    examples = []
    with open(dataset_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if len(examples) < 200:
        return {"success": False,
                "error": f"Dataset too small: {len(examples)} < 200 minimum"}

    # Count per tool
    tool_counts: dict[str, int] = {}
    for ex in examples:
        tool = ex.get("metadata", {}).get("tool", ex.get("tool", "unknown"))
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

    # Split 90/10
    random.seed(42)
    random.shuffle(examples)
    split_idx = int(len(examples) * 0.9)
    train = examples[:split_idx]
    val = examples[split_idx:]

    # Save splits
    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    train_path = EVALS_DIR / "train.jsonl"
    val_path = EVALS_DIR / "val.jsonl"

    for path, data in [(train_path, train), (val_path, val)]:
        with open(path, "w", encoding="utf-8") as f:
            for ex in data:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    result = {
        "success": True,
        "total": len(examples),
        "train_count": len(train),
        "eval_count": len(val),
        "per_tool": tool_counts,
        "train_path": str(train_path),
        "val_path": str(val_path),
    }

    if dry_run:
        result["dry_run"] = True
        return result

    # SCP to HomeLab
    _ssh(f"mkdir -p {HOMELAB_DIR}/dataset")
    ok_train = _scp(str(train_path), f"{HOMELAB_DIR}/dataset/train.jsonl")
    ok_val = _scp(str(val_path), f"{HOMELAB_DIR}/dataset/val.jsonl")

    if not (ok_train and ok_val):
        result["success"] = False
        result["error"] = "SCP failed"

    _log({"phase": 1, "action": "complete", **result})
    return result


# ── Phase 2: Fine-tune ─────────────────────────────────────────


def _generate_train_script() -> str:
    """Generate the Python training script to run on HomeLab."""
    cfg = TRAIN_CONFIG
    return f'''#!/usr/bin/env python3
"""Auto-generated fine-tune script for HomeLab RTX 3090."""
import json
from pathlib import Path

try:
    from unsloth import FastLanguageModel
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "unsloth", "torch", "transformers", "datasets", "trl"])
    from unsloth import FastLanguageModel

from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments
from runtime_utils import now

DATASET_DIR = Path.home() / "tenazas_finetune" / "dataset"
OUTPUT_DIR = Path.home() / "tenazas_finetune" / "output" / "{cfg['output_name']}"

# Load model with Unsloth optimizations
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{cfg['base_model']}",
    max_seq_length={cfg['max_seq_length']},
    dtype=None,  # auto-detect
    load_in_4bit=True,
)

# Apply LoRA
model = FastLanguageModel.get_peft_model(
    model,
    r={cfg['lora_rank']},
    lora_alpha={cfg['lora_alpha']},
    target_modules={cfg['target_modules']},
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
)

# Load dataset
dataset = load_dataset("json", data_files={{
    "train": str(DATASET_DIR / "train.jsonl"),
    "validation": str(DATASET_DIR / "val.jsonl"),
}})

def format_chat(example):
    """Format ChatML messages into a single training string."""
    msgs = example.get("messages", [])
    text = ""
    for msg in msgs:
        role = msg["role"]
        content = msg["content"]
        text += f"<|im_start|>{{role}}\\n{{content}}<|im_end|>\\n"
    return {{"text": text}}

dataset = dataset.map(format_chat)

# Training
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset["train"],
    eval_dataset=dataset["validation"],
    dataset_text_field="text",
    max_seq_length={cfg['max_seq_length']},
    args=TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        per_device_train_batch_size={cfg['batch_size']},
        gradient_accumulation_steps={cfg['gradient_accumulation']},
        learning_rate={cfg['learning_rate']},
        num_train_epochs={cfg['epochs']},
        warmup_steps=10,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        bf16=True,
        optim="adamw_8bit",
        seed=42,
    ),
)

trainer.train()

# Save merged model
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
model.save_pretrained_merged(str(OUTPUT_DIR / "merged"), tokenizer)
print(f"Model saved to {{OUTPUT_DIR / 'merged'}}")

# Export GGUF
model.save_pretrained_gguf(
    str(OUTPUT_DIR / "gguf"),
    tokenizer,
    quantization_method="{cfg['quantization']}",
)
print(f"GGUF exported to {{OUTPUT_DIR / 'gguf'}}")
print("TRAINING COMPLETE")
'''


def phase_finetune(dry_run: bool = False) -> dict:
    """Run fine-tuning on HomeLab via SSH.

    Returns: {success, output_path, duration_s}
    """
    _log({"phase": 2, "action": "start"})

    script = _generate_train_script()
    remote_script = f"{HOMELAB_DIR}/train_script.py"

    if dry_run:
        return {
            "success": True, "dry_run": True,
            "script_preview": script[:500] + "...",
        }

    # Write script to temp file and SCP
    tmp = EVALS_DIR / "_train_script.py"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        f.write(script)

    _ssh(f"mkdir -p {HOMELAB_DIR}")
    if not _scp(str(tmp), remote_script):
        return {"success": False, "error": "Failed to copy training script"}

    # Run training (long timeout: 2 hours)
    _notify("Fine-tune Phase 2: Entrenamiento iniciado en HomeLab...")
    start = time.time()
    code, stdout, stderr = _ssh(
        f"cd {HOMELAB_DIR} && python {remote_script}",
        timeout=7200,
    )
    duration = time.time() - start

    success = code == 0 and "TRAINING COMPLETE" in stdout
    result = {
        "success": success,
        "exit_code": code,
        "duration_s": round(duration),
        "stdout_tail": stdout[-1000:] if stdout else "",
        "stderr_tail": stderr[-500:] if stderr else "",
    }

    _log({"phase": 2, "action": "complete", **result})

    if success:
        _notify(f"Fine-tune completado en {duration/60:.0f} min")
    else:
        _notify(f"Fine-tune FALLIDO (code={code})\n{stderr[-200:]}")

    # Cleanup temp
    tmp.unlink(missing_ok=True)

    return result


# ── Phase 3: Deploy ─────────────────────────────────────────────


def _generate_modelfile() -> str:
    """Generate Ollama Modelfile content."""
    cfg = TRAIN_CONFIG
    name = cfg["output_name"]
    quant = cfg["quantization"].replace("_", "-").upper()
    return f"""FROM ./{name}-{quant}.gguf
SYSTEM "Sos Tenazas, asistente personal de Sebastian. Respondé siempre con JSON válido indicando el tool a ejecutar."
PARAMETER temperature 0.1
PARAMETER num_ctx 4096
"""


def phase_deploy(dry_run: bool = False) -> dict:
    """Deploy fine-tuned model to Ollama on HomeLab.

    Returns: {success, model_name, test_output}
    """
    _log({"phase": 3, "action": "start"})
    cfg = TRAIN_CONFIG
    model_name = cfg["output_name"]
    gguf_dir = f"{HOMELAB_DIR}/output/{model_name}/gguf"

    if dry_run:
        return {
            "success": True, "dry_run": True,
            "model_name": model_name,
            "modelfile": _generate_modelfile(),
        }

    # Write Modelfile
    modelfile = _generate_modelfile()
    tmp = EVALS_DIR / "_Modelfile"
    with open(tmp, "w") as f:
        f.write(modelfile)
    _scp(str(tmp), f"{gguf_dir}/Modelfile")

    # Create Ollama model
    _notify("Fine-tune Phase 3: Creando modelo en Ollama...")
    code, stdout, stderr = _ssh(
        f"cd {gguf_dir} && ollama create {model_name} -f Modelfile",
        timeout=600,
    )

    if code != 0:
        _log({"phase": 3, "action": "ollama_create_failed",
              "stderr": stderr[-500:]})
        _notify(f"Deploy FALLIDO: ollama create error\n{stderr[-200:]}")
        tmp.unlink(missing_ok=True)
        return {"success": False, "error": "ollama create failed",
                "stderr": stderr[-500:]}

    # Verify with test query
    code, stdout, stderr = _ssh(
        f'ollama run {model_name} "prendé las luces"',
        timeout=60,
    )

    test_ok = code == 0 and stdout.strip()
    result = {
        "success": test_ok,
        "model_name": model_name,
        "test_output": stdout[:500] if stdout else stderr[:500],
    }

    _log({"phase": 3, "action": "complete", **result})

    if test_ok:
        _notify(f"Modelo {model_name} activo en Ollama")
    else:
        _notify(f"Deploy: test fallido\n{(stderr or stdout)[:200]}")

    tmp.unlink(missing_ok=True)
    return result


# ── Phase 4: Eval + Rollback ───────────────────────────────────


def _load_last_eval() -> dict | None:
    """Load the most recent eval results."""
    if not EVALS_DIR.exists():
        return None
    evals = sorted(EVALS_DIR.glob("eval_results_*.json"), reverse=True)
    if not evals:
        return None
    try:
        with open(evals[0]) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def phase_eval(dataset_path: str, dry_run: bool = False) -> dict:
    """Run eval and compare with previous results.

    Returns: {success, improved, old_f1, new_f1, action}
    """
    _log({"phase": 4, "action": "start"})

    old_eval = _load_last_eval()
    old_f1 = old_eval.get("avg_f1", 0.0) if old_eval else 0.0

    if dry_run:
        return {
            "success": True, "dry_run": True,
            "old_f1": old_f1, "new_f1": "pending",
        }

    # Run eval
    sys.path.insert(0, str(BASE_DIR))
    try:
        from eval_runner import run_eval
        new_result = run_eval(dataset_path)
    except ImportError:
        return {"success": False, "error": "eval_runner not found"}

    if "error" in new_result:
        return {"success": False, "error": new_result["error"]}

    new_f1 = new_result.get("avg_f1", 0.0)
    improved = new_f1 > old_f1

    # Save eval results
    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    ts = now().strftime("%Y%m%d_%H%M%S")
    eval_path = EVALS_DIR / f"eval_results_{ts}.json"
    with open(eval_path, "w") as f:
        json.dump(new_result, f, indent=2, ensure_ascii=False)

    result = {
        "success": True,
        "improved": improved,
        "old_f1": old_f1,
        "new_f1": new_f1,
        "delta": round(new_f1 - old_f1, 4),
        "eval_path": str(eval_path),
    }

    if improved:
        result["action"] = "model_promoted"
        _notify(
            f"Fine-tune EXITOSO\n"
            f"F1: {old_f1:.1%} → {new_f1:.1%} (+{new_f1-old_f1:.1%})\n"
            f"Nuevo modelo activo."
        )
    else:
        result["action"] = "rollback"
        fail_path = EVALS_DIR / f"finetune_failed_{ts}.json"
        with open(fail_path, "w") as f:
            json.dump({
                "reason": "F1 did not improve",
                "old_f1": old_f1,
                "new_f1": new_f1,
                "eval": new_result,
                "timestamp": now().isoformat(),
            }, f, indent=2, ensure_ascii=False)

        result["fail_report"] = str(fail_path)
        _notify(
            f"Fine-tune NO mejoró\n"
            f"F1: {old_f1:.1%} → {new_f1:.1%}\n"
            f"Modelo anterior sigue activo."
        )

    _log({"phase": 4, "action": "complete", **result})
    return result


# ── Main Pipeline ──────────────────────────────────────────────


def run_pipeline(
    dataset: str | None = None,
    skip_phase: int = 0,
    dry_run: bool = False,
    auto_approve: bool = False,
):
    """Run the full fine-tuning pipeline."""
    dataset_path = dataset or str(DEFAULT_DATASET)
    results = {}

    _log({"action": "pipeline_start", "dataset": dataset_path,
          "skip_phase": skip_phase, "dry_run": dry_run})
    _notify(
        f"Fine-tune pipeline iniciado\n"
        f"Dataset: {Path(dataset_path).name}\n"
        f"{'DRY RUN' if dry_run else 'LIVE'}"
    )

    # Phase 1: Prepare
    if skip_phase < 2:
        details = f"Validar dataset, split 90/10, copiar a HomeLab\nDataset: {dataset_path}"
        if not _confirm("1 — Prepare", details, auto=auto_approve, dry_run=dry_run):
            print("Abortado por usuario.")
            return {"aborted": True, "phase": 1}

        print("\n Phase 1: Preparando dataset...")
        r1 = phase_prepare(dataset_path, dry_run=dry_run)
        results["phase_1"] = r1

        if not r1.get("success"):
            print(f" Error: {r1.get('error')}")
            return {"error": r1.get("error"), "phase": 1, "results": results}

        print(f" Train: {r1.get('train_count')} | Val: {r1.get('eval_count')}")
        for tool, count in r1.get("per_tool", {}).items():
            print(f"   {tool}: {count}")

    # Phase 2: Fine-tune
    if skip_phase < 3:
        cfg = TRAIN_CONFIG
        details = (
            f"LoRA r={cfg['lora_rank']} alpha={cfg['lora_alpha']} "
            f"epochs={cfg['epochs']}\n"
            f"Base: {cfg['base_model']}\n"
            f"Host: {HOMELAB_HOST} (RTX 3090)"
        )
        if not _confirm("2 — Fine-tune", details, auto=auto_approve, dry_run=dry_run):
            print("Abortado por usuario.")
            return {"aborted": True, "phase": 2, "results": results}

        print("\n Phase 2: Fine-tuning en HomeLab...")
        r2 = phase_finetune(dry_run=dry_run)
        results["phase_2"] = r2

        if not r2.get("success"):
            print(f" Error: {r2.get('error', r2.get('stderr_tail', ''))}")
            return {"error": "fine-tune failed", "phase": 2, "results": results}

        print(f" Completado en {r2.get('duration_s', 0)}s")

    # Phase 3: Deploy
    if skip_phase < 4:
        details = f"Crear {TRAIN_CONFIG['output_name']} en Ollama + test"
        if not _confirm("3 — Deploy", details, auto=auto_approve, dry_run=dry_run):
            print("Abortado por usuario.")
            return {"aborted": True, "phase": 3, "results": results}

        print("\n Phase 3: Deploying a Ollama...")
        r3 = phase_deploy(dry_run=dry_run)
        results["phase_3"] = r3

        if not r3.get("success"):
            print(f" Error: {r3.get('error')}")
            return {"error": "deploy failed", "phase": 3, "results": results}

        print(f" Modelo activo: {r3.get('model_name')}")

    # Phase 4: Eval (auto, no confirmation needed)
    print("\n Phase 4: Evaluando...")
    r4 = phase_eval(dataset_path, dry_run=dry_run)
    results["phase_4"] = r4

    old_f1 = r4.get("old_f1", 0)
    new_f1 = r4.get("new_f1", 0)
    if r4.get("dry_run"):
        print(f" (dry-run) Old F1: {old_f1:.1%}, new F1: pending")
    elif r4.get("improved"):
        print(f" F1 mejoró: {old_f1:.1%} → {new_f1:.1%}")
    else:
        print(f" F1 no mejoró: {old_f1:.1%} → {new_f1:.1%}")
        print(f" Modelo anterior sigue activo.")

    _log({"action": "pipeline_complete", "results": {
        k: {kk: vv for kk, vv in v.items() if kk != "script_preview"}
        for k, v in results.items()
    }})

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fine-Tune Pipeline — end-to-end model training",
    )
    parser.add_argument("--dataset", default=None,
                        help="Path to JSONL dataset")
    parser.add_argument("--skip-phase", type=int, default=0,
                        help="Skip to phase N (1-4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without executing")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Skip confirmation prompts")

    args = parser.parse_args()
    results = run_pipeline(
        dataset=args.dataset,
        skip_phase=args.skip_phase,
        dry_run=args.dry_run,
        auto_approve=args.auto_approve,
    )

    if args.dry_run:
        print("\n DRY RUN — plan completo:")
        print(json.dumps(results, indent=2, default=str))
