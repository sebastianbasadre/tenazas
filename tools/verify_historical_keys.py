"""Read historical keys in memory and check issuer authentication, without logging keys."""

import json
import re
import subprocess
from datetime import datetime, timezone

import requests


def main():
    patterns = {
        "google": r"AIza[0-9A-Za-z_-]{35}",
        "openrouter": r"sk-or-v1-[0-9a-f]{64}",
    }
    found = {provider: {} for provider in patterns}
    for commit in ("1b380c4", "3e82d95"):
        data = subprocess.check_output(["git", "show", f"{commit}:docker-compose.yml"], text=True)
        for provider, pattern in patterns.items():
            for key in re.findall(pattern, data):
                found[provider].setdefault(key, []).append(commit)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    for provider, keys in found.items():
        for key, commits in keys.items():
            entry = {"provider": provider, "commits": commits}
            try:
                if provider == "google":
                    response = requests.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        headers={"x-goog-api-key": key}, timeout=20,
                    )
                else:
                    response = requests.get("https://openrouter.ai/api/v1/key",
                                            headers={"Authorization": "Bearer " + key}, timeout=20)
                entry["http_status"] = response.status_code
                entry["authentication_accepted"] = response.status_code == 200
                reasons = []
                if provider == "google":
                    for detail in response.json().get("error", {}).get("details", []):
                        reason = detail.get("reason", "")
                        if re.fullmatch(r"[A-Z_]{1,80}", reason):
                            reasons.append(reason)
                entry["reasons"] = reasons
                entry["revocation_confirmed"] = False
                entry["note"] = "HTTP rejection alone does not prove administrative revocation"
            except Exception as exc:
                entry["error_type"] = type(exc).__name__
            report["checks"].append(entry)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
