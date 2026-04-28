#!/usr/bin/env python3
"""
Vision Pipeline — Image analysis with model chaining for Tenazas.

Two-stage pipeline:
  1. Extraction: qwen2.5vl:7b describes what's in the image
  2. Analysis: deepseek-r1:14b reasons about the extraction

Accepts images from:
  - Telegram photo file_id (downloads via Bot API)
  - Local file path
  - Raw base64 string

Does NOT rely on prompt keywords for vision routing — detection is based
on the presence of image data, not text content.

Usage:
  # From Python
  from runtime.vision_pipeline import process_image
  result = process_image(file_path="/path/to/image.jpg", prompt="qué es esto")
  result = process_image(telegram_file_id="AgACAgIA...", prompt="analizá")
  result = process_image(base64_data="iVBOR...", prompt="describí")

  # CLI
  python3 /home/tenazas/runtime/vision_pipeline.py --file /path/to/img.jpg
  python3 /home/tenazas/runtime/vision_pipeline.py --file /path/to/img.jpg --prompt "qué ves"

Security: read-only from Telegram API. Does not store images permanently.
"""

import base64
import json
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from runtime_utils import now

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

# ── Config ──────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "vision_pipeline.log"

OLLAMA_URL = OLLAMA_GPU_URL
VL_MODEL = "qwen2.5vl:7b"
ANALYSIS_MODEL = "deepseek-r1:14b"

# Telegram Bot API (token loaded at runtime from openclaw.json)
TELEGRAM_API = "https://api.telegram.org"
OPENCLAW_CONFIG = Path("/home/tenazas/.openclaw/openclaw.json")

DEFAULT_EXTRACTION_PROMPT = "Describí en detalle qué se ve en esta imagen. Sé específico con textos, números, objetos y contexto."
DEFAULT_ANALYSIS_PROMPT = "Basándote en la descripción de la imagen, analizá el contenido y extraé información estructurada relevante."

# Limits
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
EXTRACTION_TIMEOUT = 120  # seconds
ANALYSIS_TIMEOUT = 120



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Image Input Handling ────────────────────────────────────────


def _get_bot_token() -> str | None:
    """Load Telegram bot token from openclaw.json."""
    try:
        with open(OPENCLAW_CONFIG) as f:
            config = json.load(f)
        return config.get("channels", {}).get("telegram", {}).get("botToken")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def download_telegram_photo(file_id: str) -> bytes | None:
    """Download a photo from Telegram via Bot API.

    Args:
        file_id: Telegram file_id from the photo message

    Returns:
        Raw image bytes, or None on failure.
    """
    token = _get_bot_token()
    if not token:
        _log({"action": "telegram_download", "error": "no bot token"})
        return None

    # Step 1: Get file path from Telegram
    try:
        url = f"{TELEGRAM_API}/bot{token}/getFile?file_id={file_id}"
        resp = urlopen(url, timeout=15)
        data = json.loads(resp.read())
        if not data.get("ok"):
            _log({"action": "telegram_download", "error": "getFile failed", "response": data})
            return None
        file_path = data["result"]["file_path"]
    except (URLError, json.JSONDecodeError, KeyError) as e:
        _log({"action": "telegram_download", "error": f"getFile: {e}"})
        return None

    # Step 2: Download the file
    try:
        download_url = f"{TELEGRAM_API}/file/bot{token}/{file_path}"
        resp = urlopen(download_url, timeout=30)
        image_bytes = resp.read()
        if len(image_bytes) > MAX_IMAGE_SIZE:
            _log({"action": "telegram_download", "error": "image too large",
                  "size": len(image_bytes)})
            return None
        _log({"action": "telegram_download", "size": len(image_bytes), "path": file_path})
        return image_bytes
    except URLError as e:
        _log({"action": "telegram_download", "error": f"download: {e}"})
        return None


def load_image_file(path: str) -> bytes | None:
    """Load an image from a local file path.

    Returns:
        Raw image bytes, or None on failure.
    """
    try:
        p = Path(path)
        if not p.exists():
            _log({"action": "load_file", "error": f"not found: {path}"})
            return None
        data = p.read_bytes()
        if len(data) > MAX_IMAGE_SIZE:
            _log({"action": "load_file", "error": "too large", "size": len(data)})
            return None
        return data
    except OSError as e:
        _log({"action": "load_file", "error": str(e)})
        return None


def image_to_base64(image_bytes: bytes) -> str:
    """Convert raw image bytes to base64 string."""
    return base64.b64encode(image_bytes).decode("utf-8")


def resolve_image(
    file_path: str | None = None,
    telegram_file_id: str | None = None,
    base64_data: str | None = None,
) -> str | None:
    """Resolve image input to a base64 string.

    Tries inputs in order: base64_data > file_path > telegram_file_id

    Returns:
        base64 string, or None if no valid image found.
    """
    if base64_data:
        return base64_data

    if file_path:
        data = load_image_file(file_path)
        if data:
            return image_to_base64(data)

    if telegram_file_id:
        data = download_telegram_photo(telegram_file_id)
        if data:
            return image_to_base64(data)

    return None


# ── Ollama Calls ────────────────────────────────────────────────


def call_vision_model(
    base64_image: str,
    prompt: str = DEFAULT_EXTRACTION_PROMPT,
    model: str = VL_MODEL,
    timeout: int = EXTRACTION_TIMEOUT,
) -> str:
    """Call a vision-capable Ollama model with an image.

    Args:
        base64_image: base64-encoded image data
        prompt: text prompt for the model
        model: model name (must support vision)
        timeout: request timeout in seconds

    Returns:
        Model response text, or empty string on failure.
    """
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [base64_image],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }).encode()

    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        response = data.get("response", "")
        _log({"action": "vision_call", "model": model,
              "response_len": len(response),
              "prompt_len": len(prompt)})
        return response
    except (URLError, json.JSONDecodeError, OSError) as e:
        _log({"action": "vision_call", "model": model, "error": str(e)})
        return ""


def call_analysis_model(
    extraction: str,
    prompt: str = DEFAULT_ANALYSIS_PROMPT,
    context: str = "",
    model: str = ANALYSIS_MODEL,
    timeout: int = ANALYSIS_TIMEOUT,
) -> str:
    """Call the analysis model to reason about the extraction.

    Args:
        extraction: text output from the vision model
        prompt: analysis prompt
        context: additional user context
        model: model for analysis
        timeout: request timeout

    Returns:
        Analysis response text, or empty string on failure.
    """
    full_prompt = f"""Descripción de la imagen:
{extraction}

{f'Contexto del usuario: {context}' if context else ''}

{prompt}"""

    payload = json.dumps({
        "model": model,
        "prompt": full_prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }).encode()

    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        response = data.get("response", "")
        _log({"action": "analysis_call", "model": model,
              "response_len": len(response),
              "extraction_len": len(extraction)})
        return response
    except (URLError, json.JSONDecodeError, OSError) as e:
        _log({"action": "analysis_call", "model": model, "error": str(e)})
        return ""


# ── Main Pipeline ───────────────────────────────────────────────


def process_image(
    file_path: str | None = None,
    telegram_file_id: str | None = None,
    base64_data: str | None = None,
    prompt: str = "",
    analyze: bool = True,
) -> dict:
    """Run the full vision pipeline: resolve → extract → analyze.

    Args:
        file_path: local image file path
        telegram_file_id: Telegram photo file_id
        base64_data: pre-encoded base64 image
        prompt: user's question about the image
        analyze: if True, chain to deepseek for analysis (default True)

    Returns:
        {
            "status": "ok" | "error",
            "extraction": str,      # VL model's description
            "analysis": str,        # deepseek analysis (if analyze=True)
            "models_used": list,    # ["qwen2.5vl:7b", "deepseek-r1:14b"]
            "error": str | None,
        }
    """
    _log({"action": "pipeline_start",
          "has_file": file_path is not None,
          "has_telegram": telegram_file_id is not None,
          "has_base64": base64_data is not None,
          "prompt_len": len(prompt)})

    # Step 1: Resolve image to base64
    b64 = resolve_image(file_path, telegram_file_id, base64_data)
    if not b64:
        return {
            "status": "error",
            "extraction": "",
            "analysis": "",
            "models_used": [],
            "error": "No image provided or failed to load",
        }

    # Step 2: Extract with VL model
    extraction_prompt = prompt if prompt else DEFAULT_EXTRACTION_PROMPT
    extraction = call_vision_model(b64, extraction_prompt)

    if not extraction:
        return {
            "status": "error",
            "extraction": "",
            "analysis": "",
            "models_used": [VL_MODEL],
            "error": f"{VL_MODEL} returned no response — HomeLab may be offline",
        }

    models_used = [VL_MODEL]

    # Step 3: Analyze with deepseek (optional)
    analysis = ""
    if analyze:
        analysis_prompt = DEFAULT_ANALYSIS_PROMPT
        if prompt:
            analysis_prompt = f"El usuario preguntó: \"{prompt}\"\n\nBasándote en la descripción de la imagen, respondé de forma concreta y útil."
        analysis = call_analysis_model(extraction, analysis_prompt, context=prompt)
        if analysis:
            models_used.append(ANALYSIS_MODEL)

    result = {
        "status": "ok",
        "extraction": extraction,
        "analysis": analysis,
        "models_used": models_used,
        "error": None,
    }

    _log({"action": "pipeline_complete",
          "extraction_len": len(extraction),
          "analysis_len": len(analysis),
          "models_used": models_used})

    return result


def format_response(result: dict) -> str:
    """Format pipeline result for Telegram output."""
    if result["status"] == "error":
        return f"Error de vision: {result['error']}"

    parts = []

    if result["extraction"]:
        parts.append(result["extraction"])

    if result["analysis"]:
        parts.append(f"\n---\n{result['analysis']}")

    if not parts:
        return "No pude analizar la imagen."

    return "\n".join(parts)


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tenazas Vision Pipeline")
    parser.add_argument("--file", type=str, help="Local image file path")
    parser.add_argument("--telegram", type=str, help="Telegram file_id")
    parser.add_argument("--prompt", type=str, default="", help="Question about the image")
    parser.add_argument("--extract-only", action="store_true",
                        help="Only extract, skip analysis")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if not args.file and not args.telegram:
        parser.print_help()
        print("\nEjemplos:")
        print('  --file /path/to/image.jpg')
        print('  --file /path/to/image.jpg --prompt "qué es esto"')
        print('  --telegram AgACAgIA... --prompt "analizá esta captura"')
        exit(1)

    result = process_image(
        file_path=args.file,
        telegram_file_id=args.telegram,
        prompt=args.prompt,
        analyze=not args.extract_only,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_response(result))
