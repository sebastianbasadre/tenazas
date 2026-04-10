#!/usr/bin/env python3
"""
convert_to_gguf.py — Convert merged HF model to GGUF format for Ollama.

Wraps llama.cpp's convert_hf_to_gguf.py.

Usage:
    python convert_to_gguf.py \
        --merged tenazas-tools-3b-merged \
        --output tenazas-tools-3b-v3.gguf \
        --quantize q8_0
"""

import argparse
import subprocess
import sys
from pathlib import Path

PYTHON = r"C:\Python314\python.exe"
CONVERTER = r"C:\llama.cpp\convert_hf_to_gguf.py"


def main():
    p = argparse.ArgumentParser(description="Convert merged model to GGUF")
    p.add_argument("--merged", required=True, help="Path to merged HF model dir")
    p.add_argument("--output", required=True, help="Output GGUF filename")
    p.add_argument("--quantize", default="q8_0",
                    help="Quantization type (default: q8_0)")
    p.add_argument("--python", default=PYTHON, help="Python executable")
    p.add_argument("--converter", default=CONVERTER, help="Path to convert_hf_to_gguf.py")
    args = p.parse_args()

    merged_path = Path(args.merged)
    output_path = Path(args.output)

    if not merged_path.exists():
        print(f"[gguf] ERROR: merged dir not found: {merged_path}")
        sys.exit(1)

    if not Path(args.converter).exists():
        print(f"[gguf] ERROR: converter not found: {args.converter}")
        print("[gguf] Install llama.cpp and set --converter path")
        sys.exit(1)

    # Ensure output has .gguf extension
    if not output_path.suffix == ".gguf":
        output_path = output_path.with_suffix(".gguf")

    cmd = [
        args.python, args.converter,
        str(merged_path),
        "--outfile", str(output_path),
        "--outtype", args.quantize,
    ]

    print(f"[gguf] Converting {merged_path} → {output_path}")
    print(f"[gguf] Quantization: {args.quantize}")
    print(f"[gguf] Command: {' '.join(cmd)}")

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[gguf] ERROR: conversion failed")
        sys.exit(1)

    if not output_path.exists():
        print(f"[gguf] ERROR: output not generated: {output_path}")
        sys.exit(1)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[gguf] OK: {output_path} ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
