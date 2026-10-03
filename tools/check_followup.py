"""Read-only checks for the Telegram, IOL and scheduler follow-up."""

import json
import subprocess
from pathlib import Path
from urllib.request import urlopen

import homelab_goals
import iol_dependency
import telegram_router


def main():
    command = (
        "const {DatabaseSync}=require('node:sqlite');"
        "const db=new DatabaseSync('/home/node/.n8n/database.sqlite',{readOnly:true});"
        "console.log(JSON.stringify(db.prepare('SELECT active,count(*) AS count FROM workflow_entity GROUP BY active').all()));"
        "db.close();"
    )
    workflows = json.loads(subprocess.check_output(["docker", "exec", "n8n", "node", "-e", command],
                                                   text=True, stderr=subprocess.DEVNULL))
    dependency = iol_dependency.status()
    with urlopen("http://127.0.0.1:9109/metrics", timeout=5) as response:
        metrics = response.read().decode()
    state = json.loads(Path("/home/tenazas/runtime/logs/service_state.json").read_text())
    print(json.dumps({
        "telegram_status": telegram_router._slash_command_response("/status"),
        "configured_models": telegram_router._slash_command_response("/model"),
        "container_health_goal": homelab_goals._eval_containers_healthy(),
        "iol": {key: dependency.get(key) for key in ("degraded", "last_http_status", "auth_ok", "portfolio_ok", "account_ok")},
        "iol_metric": [line for line in metrics.splitlines() if line.startswith("tenazas_iol_degraded ")],
        "n8n_workflows": workflows,
        "scheduler_present": "runtime_cycle" in state["services"],
    }, indent=2))


if __name__ == "__main__":
    main()
