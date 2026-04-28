"""
google_cast.py — Google Cast speaker control via pychromecast.

Devices:
  Google Nest Mini (Living): 192.168.2.62
  Google Home Mini (Cocina): 192.168.2.83

Dependency: pychromecast (already installed)
"""

from __future__ import annotations

import logging
import re

try:
    from runtime_config import HOME_MINI_IP, NEST_MINI_IP
except ImportError:
    from workspace.runtime.runtime_config import HOME_MINI_IP, NEST_MINI_IP

logger = logging.getLogger(__name__)

DEVICE_ID = "google_cast"
DEVICE_NAME = "Google Cast"

ACTIONS = {
    "play": "Reanudar reproduccion",
    "pause": "Pausar reproduccion",
    "stop": "Detener reproduccion",
    "volumen": "Ajustar volumen (0-100)",
    "mute": "Silenciar",
    "estado": "Estado actual del speaker",
}

_SPEAKERS = {
    "living": {"name": "Nest Mini (Living)", "ip": NEST_MINI_IP},
    "cocina": {"name": "Home Mini (Cocina)", "ip": HOME_MINI_IP},
}


def _detect_speaker(query: str) -> str:
    """Detect which speaker. Default: living."""
    q = query.lower()
    if re.search(r"cocina|kitchen", q):
        return "cocina"
    return "living"


def _get_chromecast(ip: str):
    """Get a Chromecast instance by IP."""
    try:
        import pychromecast
    except ImportError:
        return None, "pychromecast no instalado."

    try:
        casts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=None, known_hosts=[ip])
        browser.stop_discovery()
        if not casts:
            return None, f"No se encontro Cast en {ip}"
        cast = casts[0]
        cast.wait(timeout=5)
        return cast, None
    except Exception as e:
        return None, str(e)


def status(config: dict) -> dict:
    """Get status of Cast speakers."""
    lines = ["Google Cast:"]
    for key, spk in _SPEAKERS.items():
        cast, err = _get_chromecast(spk["ip"])
        if err:
            lines.append(f"  {spk['name']}: offline ({err})")
            continue
        mc = cast.media_controller
        vol = int(cast.status.volume_level * 100)
        muted = cast.status.volume_muted
        if mc.status.player_state and mc.status.player_state != "UNKNOWN":
            state = mc.status.player_state
            title = mc.status.title or ""
            lines.append(f"  {spk['name']}: {state} - {title} (vol {vol}%"
                         f"{', mute' if muted else ''})")
        else:
            lines.append(f"  {spk['name']}: idle (vol {vol}%"
                         f"{', mute' if muted else ''})")
        cast.disconnect()

    return _ok("\n".join(lines), destructive=False)


def execute(query: str, config: dict) -> dict:
    """Execute a Cast action based on query text."""
    q = query.lower()
    speaker_key = _detect_speaker(query)
    spk = _SPEAKERS[speaker_key]

    cast, err = _get_chromecast(spk["ip"])
    if err:
        return _error(f"{spk['name']} no accesible: {err}")

    try:
        mc = cast.media_controller

        # Volume set
        vol_match = re.search(r"volumen?\s*(?:a|en|al?)?\s*(\d+)", q)
        if vol_match:
            level = int(vol_match.group(1))
            cast.set_volume(level / 100.0)
            cast.disconnect()
            return _ok(f"{spk['name']}: volumen a {level}%")

        # Volume up/down
        if re.search(r"(sub[ií]|m[aá]s)\s*(el\s+)?vol", q):
            new_vol = min(1.0, cast.status.volume_level + 0.1)
            cast.set_volume(new_vol)
            cast.disconnect()
            return _ok(f"{spk['name']}: volumen +")

        if re.search(r"(baj[aá]|menos)\s*(el\s+)?vol", q):
            new_vol = max(0.0, cast.status.volume_level - 0.1)
            cast.set_volume(new_vol)
            cast.disconnect()
            return _ok(f"{spk['name']}: volumen -")

        # Mute
        if re.search(r"mute|silenci|mutear", q):
            cast.set_volume_muted(not cast.status.volume_muted)
            cast.disconnect()
            return _ok(f"{spk['name']}: mute toggle")

        # Play
        if re.search(r"\bplay\b|reanudar|continuar", q):
            mc.play()
            cast.disconnect()
            return _ok(f"{spk['name']}: play")

        # Pause
        if re.search(r"pausa|pause", q):
            mc.pause()
            cast.disconnect()
            return _ok(f"{spk['name']}: pausa")

        # Stop
        if re.search(r"\bstop\b|detener|parar", q):
            mc.stop()
            cast.disconnect()
            return _ok(f"{spk['name']}: stop")

        # Estado (default)
        cast.disconnect()
        return status(config)

    except Exception as e:
        try:
            cast.disconnect()
        except Exception:
            pass
        return _error(f"Error controlando {spk['name']}: {e}")


def _ok(output: str, raw: str = "", destructive: bool = True) -> dict:
    return {
        "skill": "skill:smart-home", "success": True,
        "output": output, "raw": raw,
        "destructive": destructive, "error": None,
    }


def _error(msg: str) -> dict:
    return {
        "skill": "skill:smart-home", "success": False,
        "output": msg, "raw": "", "destructive": False, "error": "cast_error",
    }
