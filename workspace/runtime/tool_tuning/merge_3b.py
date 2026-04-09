#!/usr/bin/env python3
"""
merge_3b.py — Merge LoRA adapter into qwen2.5:3b base model.

Usage (from HomeLab):
    python merge_3b.py --lora ./tenazas-tools-3b-lora --output ../tenazas-tools-3b-merged
"""

import argparse
import sys


def main():
    p = argparse.ArgumentParser(description="Merge LoRA into base 3b model")
    p.add_argument("--base", default="Qwen/Qwen2.5-3B-Instruct",
                    help="Base model name")
    p.add_argument("--lora", default="./tenazas-tools-3b-lora",
                    help="Path to LoRA adapter")
    p.add_argument("--output", default="../tenazas-tools-3b-merged",
                    help="Output directory for merged model")
    args = p.parse_args()

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install: pip install transformers peft torch")
        sys.exit(1)

    print(f"[merge] Base: {args.base}")
    print(f"[merge] LoRA: {args.lora}")
    print(f"[merge] Output: {args.output}")

    print("[merge] Loading base model...")
    model = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.float16, device_map="cpu"
    )
    tokenizer = AutoTokenizer.from_pretrained(args.base)

    print("[merge] Loading LoRA adapter...")
    model = PeftModel.from_pretrained(model, args.lora)

    print("[merge] Merging...")
    model = model.merge_and_unload()

    print(f"[merge] Saving to {args.output}...")
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    print("[merge] Done.")


if __name__ == "__main__":
    main()
