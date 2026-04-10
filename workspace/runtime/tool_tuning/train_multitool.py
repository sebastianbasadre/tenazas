#!/usr/bin/env python3
"""
train_multitool.py — LoRA/QLoRA finetuning for Qwen2.5 7B multitool caller.

Trains a LoRA adapter on the multitool dataset for deterministic XML tool
selection. Optimized for RTX 3090 (24GB VRAM).

Usage:
    python3 train_multitool.py \
        --model Qwen/Qwen2.5-7B-Instruct \
        --dataset multitool_dataset.jsonl \
        --output ./tenazas-multitool-lora \
        --epochs 3 \
        --batch-size 4 \
        --lr 2e-4

Requirements:
    pip install transformers peft trl datasets bitsandbytes accelerate
"""

import argparse
import json
import sys
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="LoRA finetuning for multitool caller")
    p.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct",
                    help="Base model name or path")
    p.add_argument("--dataset", required=True,
                    help="Path to JSONL dataset")
    p.add_argument("--output", default="./tenazas-multitool-lora",
                    help="Output directory for LoRA adapter")
    p.add_argument("--epochs", type=int, default=3,
                    help="Number of training epochs")
    p.add_argument("--batch-size", type=int, default=4,
                    help="Per-device batch size")
    p.add_argument("--lr", type=float, default=2e-4,
                    help="Learning rate")
    p.add_argument("--lora-r", type=int, default=16,
                    help="LoRA rank")
    p.add_argument("--lora-alpha", type=int, default=32,
                    help="LoRA alpha")
    p.add_argument("--max-seq-len", type=int, default=512,
                    help="Max sequence length")
    p.add_argument("--seed", type=int, default=42,
                    help="Random seed")
    p.add_argument("--gradient-accumulation", type=int, default=4,
                    help="Gradient accumulation steps")
    p.add_argument("--no-4bit", action="store_true",
                    help="Disable 4-bit quantization (use full precision)")
    p.add_argument("--device", default="auto",
                    help="Device: cuda:0, cpu, or auto (default: auto)")
    return p.parse_args()


SYSTEM_MSG = "Respond only with valid XML tool calls."


def _format_tools_xml(tools: list[str]) -> str:
    if not tools:
        return "<tools></tools>"
    return "<tools>" + "".join(f"<tool>{t}</tool>" for t in tools) + "</tools>"


def normalize_example(entry: dict) -> dict:
    """Normalize any input format to messages for training.

    Accepts: {"messages":[...]}, {"input":..,"label":..}, {"input":..,"tools":[..]}
    """
    if "messages" in entry:
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


def load_dataset_jsonl(path: str) -> list[dict]:
    """Load JSONL dataset, normalizing all entries to messages format."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(normalize_example(json.loads(line)))
    return entries


def format_for_training(entry: dict) -> str:
    """Convert a dataset entry to a single training string.

    Format: <|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n{assistant}<|im_end|>
    """
    msgs = entry["messages"]
    parts = []
    for msg in msgs:
        role = msg["role"]
        content = msg["content"]
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    return "\n".join(parts)


def main():
    args = parse_args()

    # Lazy imports — only needed when actually training
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
        print(f"Missing dependency: {e}")
        print("Install: pip install transformers peft trl datasets bitsandbytes accelerate")
        sys.exit(1)

    # Seed
    torch.manual_seed(args.seed)

    # Device detection
    if args.device == "auto":
        if torch.cuda.is_available():
            device_map = "auto"
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_mem // (1024**3)
            print(f"[train] Device: cuda:0 ({gpu_name}, {gpu_mem}GB)")
        else:
            device_map = "cpu"
            print("[train] Device: cpu (CUDA not available)")
    else:
        device_map = args.device
        if args.device.startswith("cuda") and torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_mem // (1024**3)
            print(f"[train] Device: {args.device} ({gpu_name}, {gpu_mem}GB)")
        else:
            print(f"[train] Device: {args.device}")

    use_bf16 = torch.cuda.is_available()
    use_4bit = not args.no_4bit and torch.cuda.is_available()

    print(f"[train] Model: {args.model}")
    print(f"[train] Dataset: {args.dataset}")
    print(f"[train] Output: {args.output}")
    print(f"[train] Epochs: {args.epochs}, Batch: {args.batch_size}, LR: {args.lr}")
    print(f"[train] LoRA r={args.lora_r}, alpha={args.lora_alpha}")
    print(f"[train] 4-bit: {use_4bit}, bf16: {use_bf16}")

    # Load dataset
    raw = load_dataset_jsonl(args.dataset)
    print(f"[train] Loaded {len(raw)} examples")
    texts = [format_for_training(e) for e in raw]
    dataset = Dataset.from_dict({"text": texts})

    # Tokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Quantization config
    bnb_config = None
    if use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    # Load model
    print("[train] Loading model...")
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=bnb_config,
        device_map=device_map,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if not use_4bit and use_bf16 else
                    torch.float32 if not use_4bit else None,
    )

    if use_4bit:
        model = prepare_model_for_kbit_training(model)

    # LoRA config
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

    # Training args
    training_args = TrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation,
        learning_rate=args.lr,
        fp16=False,
        bf16=use_bf16,
        logging_steps=10,
        save_strategy="epoch",
        warmup_ratio=0.1,
        lr_scheduler_type="cosine",
        seed=args.seed,
        report_to="none",
        max_grad_norm=1.0,
    )

    # Trainer
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        tokenizer=tokenizer,
        args=training_args,
        max_seq_length=args.max_seq_len,
    )

    print("[train] Starting training...")
    trainer.train()

    # Save
    print(f"[train] Saving adapter to {args.output}")
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    print("[train] Done.")

    # --- Eval post-training (Prompt 21) ---
    print("\n[train] Calculating post-training eval metrics...")
    try:
        eval_results = trainer.evaluate()
        print(f"[train] eval_loss:     {eval_results.get('eval_loss', 'N/A'):.4f}")
        print(f"[train] eval_runtime:  {eval_results.get('eval_runtime', 0):.1f}s")

        train_log = {
            "eval_loss": eval_results.get("eval_loss"),
            "train_loss": trainer.state.log_history[-1].get("loss") if trainer.state.log_history else None,
            "epochs": trainer.state.epoch,
            "global_step": trainer.state.global_step,
            "model": args.model,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }
        log_path = Path(args.output) / "training_metrics.json"
        log_path.write_text(json.dumps(train_log, indent=2))
        print(f"[train] Metrics saved to: {log_path}")
    except Exception as e:
        print(f"[train] Post-training eval failed (non-critical): {e}")


if __name__ == "__main__":
    main()
