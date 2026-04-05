#!/usr/bin/env python3
"""
Telegram Handler — Photo/image processing for Tenazas.

Downloads images from Telegram, converts to base64, and routes to the
appropriate vision backend (IOL portfolio analysis or generic Gemini).

Also manages compact-guard state to prevent context compaction from
wiping image context.

Usage:
  from runtime.telegram_handler import handle_photo, should_defer_compact

  response = handle_photo(file_id="AgACAgIA...", caption="auditá mi portfolio")
  if should_defer_compact():
      # skip compacting
"""

import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Config ──────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "telegram_handler.log"
COMPACT_GUARD_FILE = BASE_DIR / "compact_guard.json"

CONFIG_FILE = Path("/home/tenazas/.openclaw/openclaw.json")
PENDING_ORDER_FILE = Path("/home/tenazas/.openclaw/state/iol_pending_order.json")

TELEGRAM_API = "https://api.telegram.org"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-2.0-flash"

IOL_KEYWORDS = {"portfolio", "portafolio", "audita", "analiza", "posiciones",
                "cartera", "inversiones", "acciones", "cedears"}

MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Config loading ──────────────────────────────────────────────


def _get_bot_token() -> str | None:
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        return config.get("channels", {}).get("telegram", {}).get("botToken")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _get_gemini_key() -> str | None:
    """Get Gemini API key from env var (set in docker-compose)."""
    return os.environ.get("GOOGLE_API_KEY")


# ── Photo Download ──────────────────────────────────────────────


def download_telegram_photo(file_id: str) -> bytes | None:
    """Download a photo from Telegram via Bot API.

    Args:
        file_id: Telegram file_id (use photo[-1] for highest resolution)

    Returns:
        Raw image bytes, or None on failure.
    """
    token = _get_bot_token()
    if not token:
        _log({"action": "download_photo", "error": "no bot token"})
        return None

    # Step 1: Get file path
    try:
        url = f"{TELEGRAM_API}/bot{token}/getFile?file_id={file_id}"
        resp = urlopen(url, timeout=15)
        data = json.loads(resp.read())
        if not data.get("ok"):
            _log({"action": "download_photo", "error": "getFile failed"})
            return None
        file_path = data["result"]["file_path"]
    except (URLError, json.JSONDecodeError, KeyError) as e:
        _log({"action": "download_photo", "error": f"getFile: {e}"})
        return None

    # Step 2: Download file
    try:
        download_url = f"{TELEGRAM_API}/file/bot{token}/{file_path}"
        resp = urlopen(download_url, timeout=30)
        image_bytes = resp.read()
        if len(image_bytes) > MAX_IMAGE_SIZE:
            _log({"action": "download_photo", "error": "too large"})
            return None
        _log({"action": "download_photo", "size": len(image_bytes)})
        return image_bytes
    except URLError as e:
        _log({"action": "download_photo", "error": f"download: {e}"})
        return None


def photo_to_base64(image_bytes: bytes) -> str:
    """Convert raw image bytes to base64 string."""
    return base64.b64encode(image_bytes).decode("utf-8")


# ── Gemini Vision ───────────────────────────────────────────────


def _call_gemini_vision(base64_img: str, prompt: str, mime_type: str = "image/jpeg") -> str:
    """Call Gemini Vision API with an image.

    Args:
        base64_img: base64-encoded image
        prompt: text prompt
        mime_type: image MIME type

    Returns:
        Response text, or empty string on failure.
    """
    api_key = _get_gemini_key()
    if not api_key:
        _log({"action": "gemini_vision", "error": "no GOOGLE_API_KEY"})
        return ""

    url = f"{GEMINI_API}/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    payload = json.dumps({
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": base64_img}},
                {"text": prompt},
            ]
        }]
    }).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        resp = urlopen(req, timeout=60)
        data = json.loads(resp.read())
        # Extract text from Gemini response
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text_parts = [p["text"] for p in parts if "text" in p]
            result = "\n".join(text_parts)
            _log({"action": "gemini_vision", "response_len": len(result)})
            return result
        return ""
    except (URLError, HTTPError, json.JSONDecodeError) as e:
        _log({"action": "gemini_vision", "error": str(e)})
        return ""


def iol_analyze_image(base64_img: str, caption: str = "") -> str:
    """Analyze a portfolio image using Gemini Vision.

    Specialized prompt for IOL/investment analysis.

    Returns:
        Formatted analysis text for Telegram.
    """
    prompt = (
        "Sos un gestor de inversiones profesional analizando un portfolio "
        "argentino. Analizá la imagen y respondé en español con:\n"
        "1. Composición actual de la cartera\n"
        "2. Concentración de riesgo\n"
        "3. Rendimiento vs inflación estimada\n"
        "4. 3 acciones concretas recomendadas\n"
        "5. Alertas si hay algo preocupante\n"
        "Sé directo y técnico, sin suavizar los problemas."
    )
    if caption:
        prompt += f"\n\nContexto adicional del usuario: {caption}"

    _log({"action": "iol_analyze_image", "caption": caption[:50]})

    result = _call_gemini_vision(base64_img, prompt)
    if not result:
        return "No pude analizar la imagen del portfolio. Intentá de nuevo o describí las posiciones con texto."

    return f"📊 *Análisis de portfolio*\n\n{result}"


def analyze_image_gemini(base64_img: str, caption: str = "") -> str:
    """Analyze a generic image using Gemini Vision.

    Returns:
        Description/analysis text for Telegram.
    """
    prompt = "Describí y analizá lo que se ve en esta imagen. Sé específico con textos, números y contexto."
    if caption:
        prompt = f"{caption}\n\nDescribí lo que se ve en la imagen y respondé la pregunta del usuario."

    _log({"action": "analyze_image_generic", "caption": caption[:50]})

    result = _call_gemini_vision(base64_img, prompt)
    if not result:
        return "No pude procesar la imagen, intentá describirla con texto."

    return result


# ── Main Photo Handler ──────────────────────────────────────────


def _is_iol_caption(caption: str) -> bool:
    """Check if caption contains IOL-related keywords."""
    if not caption:
        return False
    caption_lower = caption.lower()
    return any(kw in caption_lower for kw in IOL_KEYWORDS)


def handle_photo(file_id: str, caption: str = "") -> str:
    """Handle an incoming Telegram photo message.

    1. Downloads image from Telegram
    2. Converts to base64
    3. Routes to IOL analysis or generic Gemini based on caption
    4. Updates compact guard

    Args:
        file_id: Telegram file_id (highest resolution)
        caption: optional photo caption text

    Returns:
        Response text for Telegram.
    """
    _log({"action": "handle_photo", "file_id": file_id[:20], "caption": caption[:50]})

    # Download
    image_bytes = download_telegram_photo(file_id)
    if not image_bytes:
        return "No pude descargar la imagen. Intentá de nuevo."

    # Convert to base64
    b64 = photo_to_base64(image_bytes)

    # Update compact guard (we just received a photo — don't compact)
    _update_compact_guard(has_photo=True)

    # Route
    if _is_iol_caption(caption):
        return iol_analyze_image(b64, caption)
    else:
        return analyze_image_gemini(b64, caption)


# ── Compact Guard ───────────────────────────────────────────────
# Prevents context compaction from wiping recent image/IOL context.


def _update_compact_guard(has_photo: bool = False, intent: str = ""):
    """Update compact guard state file."""
    state = {
        "last_message_ts": time.time(),
        "last_had_photo": has_photo,
        "last_user_intent": intent,
        "has_pending_order": PENDING_ORDER_FILE.exists(),
        "updated_at": _now().isoformat(),
    }
    COMPACT_GUARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COMPACT_GUARD_FILE, "w") as f:
        json.dump(state, f)


def should_defer_compact() -> bool:
    """Check if context compaction should be deferred.

    Returns True if any of:
    - Last message was < 60 seconds ago
    - Last message had a photo
    - There's a pending IOL order

    Call this before triggering compact.
    """
    try:
        with open(COMPACT_GUARD_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    now = time.time()

    # Recent message (< 60s)
    last_ts = state.get("last_message_ts", 0)
    if now - last_ts < 60:
        _log({"action": "defer_compact", "reason": "recent_message",
              "age": round(now - last_ts)})
        return True

    # Last message had photo
    if state.get("last_had_photo"):
        # Only guard for 120s after photo
        if now - last_ts < 120:
            _log({"action": "defer_compact", "reason": "recent_photo"})
            return True

    # Pending IOL order
    if state.get("has_pending_order") or PENDING_ORDER_FILE.exists():
        _log({"action": "defer_compact", "reason": "pending_iol_order"})
        return True

    return False


def mark_message(intent: str = "", has_photo: bool = False):
    """Mark that a user message was received (call on every message).

    This updates the compact guard so it knows about recent activity.
    """
    _update_compact_guard(has_photo=has_photo, intent=intent)
