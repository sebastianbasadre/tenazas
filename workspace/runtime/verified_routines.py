"""A reviewed cinema routine with read-back verification and rollback."""

import json
import re
import time

import requests
from devices import hue_lights as hue, marantz_avr as avr, samsung_tv as tv


def plan(name, config):
    if name.lower() not in {"cine", "cinema"}:
        raise ValueError("Rutina disponible: cine")
    lights = hue.status(config)
    receiver = avr.status(config)
    if not lights.get("success") or not receiver.get("success"):
        raise ValueError("No pude leer Hue y Marantz; no es seguro preparar cambios sin su estado previo")
    states = json.loads(lights["raw"])
    if any(lid not in states for lid in hue.LIGHT_IDS):
        raise ValueError("Faltan luces configuradas en la respuesta del Bridge")
    target = tv._detect_target("televisor q70")
    power = re.search(r"Power:\s*(\S+)", receiver["output"])
    source = re.search(r"Input:\s*([^\n]+)", receiver["output"])
    if not power or not source:
        raise ValueError("Estado de Marantz incompleto")
    return {"name": "cine", "before": {"lights": {lid: states[lid]["state"] for lid in hue.LIGHT_IDS},
             "receiver_power": power.group(1), "receiver_source": source.group(1).strip(),
             "tv_online": tv._is_online(target["ip"])},
            "text": "Modo cine: encender Q70, encender Marantz y seleccionar TV, poner luces Hue tenues. Se verificará cada dispositivo; ante fallo se intentará restaurar su estado previo.",
            "approval_required": True}


def check_lights(config, expected):
    result = hue.status(config)
    if not result.get("success"):
        return False
    actual = json.loads(result["raw"])
    for lid, state in expected.items():
        current = actual.get(lid, {}).get("state", {})
        if not current or current.get("reachable") is False:
            return False
        for key in ("on", "bri", "ct", "hue", "sat", "xy"):
            if key not in state:
                continue
            if key == "xy":
                if len(current.get(key, [])) != len(state[key]) or any(abs(a - b) > .01 for a, b in zip(current[key], state[key])):
                    return False
            elif abs(current.get(key, -10000) - state[key]) > (3 if key in {"bri", "ct", "sat"} else 100 if key == "hue" else 0):
                return False
    return True


def rollback(prepared, config, touched):
    before = prepared["before"]
    result = []
    if "lights" in touched:
        key = hue._get_hue_key(config)
        expected = {}
        for lid, state in before["lights"].items():
            fields = {"on", "bri"} | ({"ct"} if state.get("colormode") == "ct" else {"xy"} if state.get("colormode") == "xy" else {"hue", "sat"})
            expected[lid] = {field: value for field, value in state.items() if field in fields}
            response = requests.put(f"http://{hue.BRIDGE_IP}/api/{key}/lights/{lid}/state",
                                    json=expected[lid], timeout=5)
            response.raise_for_status()
        result.append("Hue: restauración verificada" if check_lights(config, expected) else "Hue: restauración no verificada")
    if "receiver" in touched:
        source = before["receiver_source"]
        if re.fullmatch(r"[A-Za-z0-9 _-]{1,24}", source):
            avr._send_command(avr._get_ip(config), "SI" + source)
        avr.execute("encender" if before["receiver_power"].upper() == "ON" else "apagar", config)
        current = avr.status(config)
        verified = current.get("success") and f"Power: {before['receiver_power']}" in current["output"] and f"Input: {source}" in current["output"]
        result.append("Marantz: restaurado y verificado" if verified else "Marantz: restauración no verificada")
    if "tv" in touched and not before["tv_online"]:
        tv.execute("apagar televisor q70", config)
        target = tv._detect_target("televisor q70")
        result.append("TV: apagada y verificada" if not tv._is_online(target["ip"]) else "TV: apagado no verificado")
    return result


def execute(prepared, config, cancelled, progress):
    touched = []
    report = []
    try:
        current_plan = plan(prepared["name"], config)
        if current_plan["before"] != prepared["before"]:
            return {"ok": False, "text": "El estado de los dispositivos cambió desde la propuesta. Prepará y aprobá una rutina nueva."}
        if cancelled():
            raise RuntimeError("Cancelado")
        target = tv._detect_target("televisor q70")
        touched.append("tv")
        response = tv.execute("encender televisor q70", config)
        if not response.get("success"):
            raise RuntimeError("TV no aceptó encendido")
        deadline = time.monotonic() + 25
        while not tv._is_online(target["ip"]):
            if cancelled() or time.monotonic() >= deadline:
                raise RuntimeError("TV no confirmó encendido")
            time.sleep(1)
        report.append("TV Q70: encendido verificado")
        progress("TV verificada")
        if cancelled():
            raise RuntimeError("Cancelado")
        touched.append("receiver")
        if not avr.execute("encender", config).get("success") or not avr.execute("input tv", config).get("success"):
            raise RuntimeError("Marantz no aceptó configuración")
        current = avr.status(config)
        if not current.get("success") or "Power: ON" not in current["output"] or not re.search(r"Input:\s*TV", current["output"]):
            raise RuntimeError("Marantz no confirmó encendido/entrada TV")
        report.append("Marantz: encendido y entrada TV verificados")
        progress("Marantz verificado")
        if cancelled():
            raise RuntimeError("Cancelado")
        touched.append("lights")
        hue.execute("modo cine", config)
        if not check_lights(config, {lid: hue.SCENES["cinema"] for lid in hue.LIGHT_IDS}):
            raise RuntimeError("Hue no confirmó la escena en todas las luces")
        report.append("Hue: escena cine verificada en todas las luces")
        if cancelled():
            raise RuntimeError("Cancelado")
        return {"text": "Modo cine terminado:\n" + "\n".join(report), "ok": True}
    except Exception as exc:
        try:
            restored = rollback(prepared, config, touched)
        except Exception as rollback_error:
            restored = ["Rollback incompleto: " + type(rollback_error).__name__]
        return {"ok": False, "text": f"Rutina detenida: {exc}\n" + "\n".join(report + restored)}
