"""Report inference destinations without credentials, prompts or account data."""

import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import runtime_config as config
from runtime_utils import load_json


def destination(url):
    parsed = urlsplit(url)
    return {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port,
            "through_proxy": parsed.hostname in ("127.0.0.1", "localhost") and parsed.port == 18902}


def main():
    root = Path("/home/tenazas/.openclaw")
    report = {"runtime_gpu": destination(config.OLLAMA_GPU_URL),
              "runtime_cpu": destination(config.OLLAMA_NAS_URL),
              "clients_using_runtime_config": ["telegram", "job-search", "finetuned-router",
                                                "model-router", "gpu-scheduler", "learning", "embeddings"]}
    providers = load_json(root / "openclaw.json", {}).get("models", {}).get("providers", {})
    report["gateway_providers"] = {name: destination(value["baseUrl"]) for name, value in providers.items()
                                   if name.startswith("ollama") and value.get("baseUrl")}
    override = load_json(root / "state/autofix_config.json", {}).get("llm_backends", {}).get("ollama_base_url")
    report["autofix"] = destination(override or config.OLLAMA_GPU_URL)
    report["observed_proxy_clients"] = load_json(Path("/home/tenazas/runtime/logs/inference_state.json"), {}).get("clients", {})
    report["other_container_destinations"] = []
    ids = subprocess.check_output(["docker", "ps", "-q"], text=True).split()
    for container in json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True)) if ids else []:
        for variable in container.get("Config", {}).get("Env", []):
            name, _, value = variable.partition("=")
            if ":11434" not in value and ":18902" not in value:
                continue
            if not value.startswith(("http://", "https://")):
                continue
            report["other_container_destinations"].append({
                "container": container["Name"].lstrip("/"), "variable": name,
                "role": "proxy-upstream" if name == "TENAZAS_OLLAMA_UPSTREAM_URL" else "client",
                "destination": destination(value),
            })
    report["limit"] = "Explicit CLI destinations, PC-local clients and other containers require a separate audit"
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
