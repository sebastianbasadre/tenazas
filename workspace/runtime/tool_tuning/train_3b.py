#!/usr/bin/env python3
"""
train_3b.py — LoRA finetuning for Qwen2.5-3B deterministic tool classifier.

Trains a strict XML tool selector: user text → <tools><tool>name</tool></tools>
Optimized for RTX 3090 (24GB VRAM). 3B model fits fully in VRAM for fast training.

Usage:
    python train_3b.py \
        --train multitool_train_v3.jsonl \
        --eval multitool_eval_v3.jsonl \
        --output tenazas-tools-3b-lora-v3

Requirements:
    pip install transformers peft trl datasets bitsandbytes accelerate
"""

import argparse
import json
import re
import sys
from pathlib import Path

ALLOWED_TOOLS = {
    "weather", "email", "calendar", "infra-monitor", "network-scanner",
    "security-monitor", "arr-monitor", "self-evolve", "reminders", "smart-home-on",
    "smart-home-off", "smart-home-cinema", "tts",
}

SYSTEM_MSG = (
    "You are a deterministic tool classifier. "
    "Output ONLY valid XML. Never output text. "
    "Allowed tools: weather, email, calendar, infra-monitor, network-scanner, "
    "security-monitor, arr-monitor, self-evolve, reminders, smart-home-on, smart-home-off, "
    "smart-home-cinema, tts. "
    "If no tools apply return <tools></tools>"
)


def _format_tools_xml(tools: list[str]) -> str:
    if not tools:
        return "<tools></tools>"
    return "<tools>" + "".join(f"<tool>{t}</tool>" for t in tools) + "</tools>"


def normalize_example(entry: dict) -> dict:
    """Normalize any input format to strict messages format."""
    if "messages" in entry:
        # Ensure system message is the strict classifier prompt
        msgs = entry["messages"]
        if msgs and msgs[0]["role"] == "system":
            msgs[0]["content"] = SYSTEM_MSG
        else:
            msgs.insert(0, {"role": "system", "content": SYSTEM_MSG})
        return entry

    user_msg = entry.get("input", "")
    if "label" in entry:
        label = entry["label"]
        tools = [] if label == "none" or not label else [label]
    elif "tools" in entry:
        tools = entry["tools"]
    else:
        tools = []

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": _format_tools_xml(tools)},
        ]
    }


def validate_entry(entry: dict) -> bool:
    """Validate that assistant output only contains allowed tools."""
    for msg in entry.get("messages", []):
        if msg.get("role") == "assistant":
            tools = re.findall(r"<tool>([^<]+)</tool>", msg["content"])
            for t in tools:
                if t not in ALLOWED_TOOLS:
                    print(f"[warn] Invalid tool in training data: {t!r}")
                    return False
    return True


def load_dataset_jsonl(path: str) -> list[dict]:
    """Load and normalize JSONL dataset."""
    entries = []
    skipped = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = normalize_example(json.loads(line))
            if validate_entry(entry):
                entries.append(entry)
            else:
                skipped += 1
    if skipped:
        print(f"[train] Skipped {skipped} invalid entries from {path}")
    return entries


def format_chatml(entry: dict) -> str:
    """Convert to ChatML training string."""
    parts = []
    for msg in entry["messages"]:
        parts.append(f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>")
    return "\n".join(parts)


def parse_args():
    p = argparse.ArgumentParser(description="LoRA finetuning for 3B tool classifier")
    p.add_argument("--base-model", default="Qwen/Qwen2.5-3B-Instruct",
                    help="Base model (default: Qwen2.5-3B-Instruct)")
    p.add_argument("--train", required=True, help="Training JSONL")
    p.add_argument("--eval", required=True, help="Eval JSONL")
    p.add_argument("--output", default="./tenazas-tools-3b-lora-v3",
                    help="Output dir for LoRA adapter")
    p.add_argument("--epochs", type=int, default=5,
                    help="Training epochs (default: 5 — classifier benefits from more)")
    p.add_argument("--batch-size", type=int, default=8,
                    help="Per-device batch size (3B fits more)")
    p.add_argument("--lr", type=float, default=2e-4, help="Learning rate")
    p.add_argument("--lora-r", type=int, default=16, help="LoRA rank")
    p.add_argument("--lora-alpha", type=int, default=32, help="LoRA alpha")
    p.add_argument("--max-seq-len", type=int, default=256,
                    help="Max seq len (256 sufficient for short tool outputs)")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--gradient-accumulation", type=int, default=2,
                    help="Gradient accumulation steps")
    p.add_argument("--no-4bit", action="store_true",
                    help="Disable 4-bit quantization")
    p.add_argument("--device", default="auto", help="Device: cuda:0, cpu, auto")
    return p.parse_args()


def main():
    args = parse_args()

    try:
        import torch
        from datasets import Dataset
        from transformers import (
            AutoModelForCausalLM, AutoTokenizer,
            BitsAndBytesConfig, TrainingArguments,
        )
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from trl import SFTTrainer
    except ImportError as e:
        print(f"Missing: {e}")
        print("pip install transformers peft trl datasets bitsandbytes accelerate")
        sys.exit(1)

    torch.manual_seed(args.seed)

    # Device
    if args.device == "auto":
        if torch.cuda.is_available():
            device_map = "auto"
            gpu = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory // (1024**3)
            print(f"[train] GPU: {gpu} ({vram}GB)")
        else:
            device_map = "cpu"
            print("[train] CPU mode")
    else:
        device_map = args.device

    use_bf16 = torch.cuda.is_available()
    use_4bit = not args.no_4bit and torch.cuda.is_available()

    print(f"[train] Model: {args.base_model}")
    print(f"[train] Train: {args.train}")
    print(f"[train] Eval:  {args.eval}")
    print(f"[train] Output: {args.output}")
    print(f"[train] Epochs: {args.epochs}, Batch: {args.batch_size}, LR: {args.lr}")
    print(f"[train] LoRA r={args.lora_r}, alpha={args.lora_alpha}")
    print(f"[train] 4-bit: {use_4bit}, bf16: {use_bf16}")

    # Load datasets
    train_raw = load_dataset_jsonl(args.train)
    eval_raw = load_dataset_jsonl(args.eval)
    print(f"[train] Train: {len(train_raw)} examples, Eval: {len(eval_raw)} examples")

    train_texts = [format_chatml(e) for e in train_raw]
    eval_texts = [format_chatml(e) for e in eval_raw]
    train_ds = Dataset.from_dict({"text": train_texts})
    eval_ds = Dataset.from_dict({"text": eval_texts})

    # Tokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Quantization
    bnb_config = None
    if use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    # Model
    print("[train] Loading model...")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        device_map=device_map,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if not use_4bit and use_bf16 else
                    torch.float32 if not use_4bit else None,
    )

    if use_4bit:
        model = prepare_model_for_kbit_training(model)

    # LoRA
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Training
    training_args = TrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation,
        learning_rate=args.lr,
        fp16=False,
        bf16=use_bf16,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        warmup_ratio=0.1,
        lr_scheduler_type="cosine",
        seed=args.seed,
        report_to="none",
        max_grad_norm=1.0,
    )
    # Compatibility for TRL/Transformers version skew: some TRL releases still
    # call TrainingArguments.to_dict() and then pop the legacy
    # push_to_hub_token key even when hub uploads are disabled.
    original_to_dict = training_args.to_dict

    def _to_dict_with_legacy_hub_token():
        data = original_to_dict()
        data.setdefault("push_to_hub_token", None)
        return data

    training_args.to_dict = _to_dict_with_legacy_hub_token

    trainer = SFTTrainer(
        model=model,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        args=training_args,

    )

    print("[train] Starting training...")
    trainer.train()

    # Save best adapter
    print(f"[train] Saving adapter to {args.output}")
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)

    # Eval metrics
    print("\n[train] Final evaluation...")
    try:
        eval_results = trainer.evaluate()
        print(f"[train] eval_loss: {eval_results.get('eval_loss', 'N/A'):.4f}")

        metrics = {
            "eval_loss": eval_results.get("eval_loss"),
            "train_loss": trainer.state.log_history[-1].get("loss")
                          if trainer.state.log_history else None,
            "epochs": trainer.state.epoch,
            "global_step": trainer.state.global_step,
            "base_model": args.base_model,
            "train_examples": len(train_raw),
            "eval_examples": len(eval_raw),
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }
        log_path = Path(args.output) / "training_metrics.json"
        log_path.write_text(json.dumps(metrics, indent=2))
        print(f"[train] Metrics: {log_path}")
    except Exception as e:
        print(f"[train] Eval failed (non-critical): {e}")

    print("[train] Done.")


if __name__ == "__main__":
    main()
