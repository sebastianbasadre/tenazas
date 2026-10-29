# HomeLab Ollama runbook

Reviewed **2026-10-28**. Commands describe the versioned startup script, not the
models currently installed. See [Models](../MODELS.md) for the runtime `ROLE_MAP`,
which uses a different model set.

Reference host: `192.168.2.6`, Windows 11, endpoint `http://192.168.2.6:11434`.

## Start Ollama

Run PowerShell on the intended HomeLab:

```powershell
$env:OLLAMA_HOST = "0.0.0.0:11434"
setx OLLAMA_HOST "0.0.0.0:11434"
ollama serve
```

Check locally:

```powershell
ollama list
ollama ps
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

Then check from the NAS:

```bash
curl -fsS http://192.168.2.6:11434/api/tags
```

A bind on `0.0.0.0` requires network rules restricting access to intended clients.

## Load the startup script's model set

```powershell
ollama pull gemma3:12b
ollama pull qwen3:8b
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Additional models referenced by runtime paths should be installed only as needed:

```powershell
ollama pull qwen3:4b
ollama pull qwen2.5-coder:7b
ollama pull qwen3-vl:4b
ollama pull phi4-mini
ollama pull qwen2.5:3b
```

Warm the startup script's primary model:

```powershell
$body = @{
  model = "gemma3:12b"
  prompt = "ok"
  stream = $false
  keep_alive = "1h"
  options = @{ num_predict = 1 }
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
  -Uri "http://127.0.0.1:11434/api/generate" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## Configure logon startup

Review [scripts/homelab-ollama-startup.ps1](../scripts/homelab-ollama-startup.ps1),
then run it on the HomeLab:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\homelab-ollama-startup.ps1
```

The script sets `OLLAMA_HOST`, starts Ollama if unavailable, pulls the first model
set above, warms `gemma3:12b`, and places a batch launcher in the user's Startup
folder. This is a logon startup mechanism, not proof of a system service running
before login.

## Check model-name differences

`qwen3-fast:4b` and `qwen3-fast:8b` in `ROLE_MAP` are custom models. The startup
script does not create them; import or recreate their Modelfiles separately.
Do not assume standard `qwen3:*` tags have identical templates or behavior.
Verify an inference using the exact configured name before enabling its path.

## Final checks

HomeLab:

```powershell
Get-Process ollama -ErrorAction SilentlyContinue
Invoke-RestMethod http://127.0.0.1:11434/api/tags
ollama ps
```

NAS:

```bash
curl -fsS http://192.168.2.6:11434/api/tags
docker exec openclaw bash /home/tenazas/ollama-check.sh --json
```
