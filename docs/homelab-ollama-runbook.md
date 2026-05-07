# HomeLab Ollama Runbook

Host: `192.168.2.6`
OS: Windows 11
Endpoint esperado: `http://192.168.2.6:11434`

## 1. Levantar Ollama

Abrí PowerShell en el HomeLab y corré:

```powershell
ollama serve
```

Verificá:

```powershell
ollama list
ollama ps
curl http://127.0.0.1:11434/api/tags
```

Si responde localmente, desde el NAS debería responder:

```bash
curl http://192.168.2.6:11434/api/tags
```

## 2. Cargar modelos

Para el set local actual:

```powershell
ollama pull qwen3:8b
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Set recomendado a futuro:

```powershell
ollama pull qwen3:4b
ollama pull qwen2.5-coder:7b
ollama pull qwen3-vl:4b
ollama pull phi4-mini
```

Precarga rápida del modelo principal:

```powershell
$body = @{
  model = "qwen3:8b"
  prompt = "ok"
  stream = $false
  options = @{ num_predict = 1 }
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
  -Uri "http://127.0.0.1:11434/api/generate" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## 3. Auto-start al boot/logon

En este repo quedó un script listo para correr en el HomeLab:

[`scripts/homelab-ollama-startup.ps1`](Z:\openclaw\scripts\homelab-ollama-startup.ps1)

Ejecutalo una vez en PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\homelab-ollama-startup.ps1
```

Eso hace:

- levanta `ollama serve` si no está arriba
- hace `pull` de `qwen3:8b`, `qwen2.5:7b`, `nomic-embed-text` si faltan
- precarga `qwen3:8b`
- instala `start-ollama.bat` en la carpeta Startup del usuario

## Verificación final

En HomeLab:

```powershell
Get-Process ollama -ErrorAction SilentlyContinue
curl http://127.0.0.1:11434/api/tags
ollama ps
```

En NAS:

```bash
curl http://192.168.2.6:11434/api/tags
docker exec openclaw bash /home/tenazas/ollama-check.sh --json
```
