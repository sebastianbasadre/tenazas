"""Configure existing OpenClaw schema fields for local GPU and CPU fallback."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/tenazas/runtime")
from runtime_utils import write_atomic


def main():
    path = Path("/home/tenazas/.openclaw/openclaw.json")
    before = json.loads(path.read_text())
    owner = path.stat()
    backup = path.parent / "deployments/20261003-24x7/openclaw.models.before.json"
    if not backup.exists():
        write_atomic(backup, before)
        os.chmod(backup, 0o600)
    config = copy.deepcopy(before)
    provider = config["models"]["providers"]["ollama"]
    provider["baseUrl"] = "http://127.0.0.1:18902"
    template = copy.deepcopy(provider["models"][0])
    if not any(model["id"] == "qwen3-fast:8b" for model in provider["models"]):
        model = copy.deepcopy(template)
        model.update(id="qwen3-fast:8b", name="Qwen3 Fast 8B", contextWindow=8192, maxTokens=2048)
        provider["models"].append(model)
    cpu = copy.deepcopy(provider)
    cpu["baseUrl"] = "http://127.0.0.1:11434"
    model = copy.deepcopy(template)
    model.update(id="qwen2.5:3b", name="Qwen2.5 CPU 3B", contextWindow=4096, maxTokens=1024)
    cpu["models"] = [model]
    config["models"]["providers"]["ollama-cpu"] = cpu
    config["agents"]["defaults"]["model"] = {
        "primary": "ollama/qwen3-fast:8b",
        "fallbacks": ["ollama-cpu/qwen2.5:3b"],
    }
    write_atomic(path, config)
    os.chown(path, owner.st_uid, owner.st_gid)
    os.chmod(path, 0o600)
    validation = subprocess.run(["openclaw", "config", "validate"], capture_output=True, text=True,
                                env={**os.environ, "OPENCLAW_CONFIG_PATH": str(path)})
    if validation.returncode:
        failure_report = backup.parent / "model-validation.log"
        failure_report.write_text(validation.stdout + validation.stderr)
        os.chmod(failure_report, 0o600)
        write_atomic(path, before)
        os.chown(path, owner.st_uid, owner.st_gid)
        os.chmod(path, 0o600)
        print("OpenClaw rejected model configuration; restored previous configuration")
        return 1
    print("Validated: Qwen3 Fast 8B on GPU; Qwen2.5 3B CPU fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
