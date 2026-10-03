# merge_3b.py — RAM-efficient merge
import argparse, gc, torch
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

def merge(args):
    device_map = args.device or "cpu"

    print("[merge] Loading tokenizer...")
    tok = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)

    print(f"[merge] Loading base model in float16 sharded on {device_map}...")
    # Clave: low_cpu_mem_usage=True reduce pico durante carga
    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=torch.float16,
        device_map=device_map,
        low_cpu_mem_usage=True,   # ← evita duplicar tensores al cargar
    )

    print("[merge] Loading LoRA adapter...")
    model = PeftModel.from_pretrained(model, args.adapter, device_map=device_map)

    print("[merge] Merging (esto pica RAM ~30s)...")
    # Forzar GC antes del pico
    gc.collect()
    model = model.merge_and_unload()
    gc.collect()

    print("[merge] Saving merged model in shards de 2GB...")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(
        str(out),
        max_shard_size="2GB",   # ← evita pico durante serialización
        safe_serialization=True,
    )
    tok.save_pretrained(str(out))
    print(f"[merge] OK → {out}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base",    default="models/base/qwen2.5-3b")
    p.add_argument("--adapter", "--lora", dest="adapter", default="models/lora")
    p.add_argument("--output",  default="models/merged")
    p.add_argument("--device",  default="cpu")
    merge(p.parse_args())
