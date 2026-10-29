# HomeLab Ollama Runbook

Revisión documental: **2026-10-28**. Los comandos describen el script versionado;
no confirman modelos instalados. Ver [MODELS.md](../MODELS.md) para el `ROLE_MAP`
del runtime, que usa un conjunto distinto.

Host: `192.168.2.6`
OS: Windows 11
Endpoint esperado: `http://192.168.2.6:11434`

## 1. Levantar Ollama

Abrí PowerShell en el HomeLab y corré:

```powershell
$env:OLLAMA_HOST = "0.0.0.0:11434"
setx OLLAMA_HOST "0.0.0.0:11434"
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

Para reproducir el conjunto de `scripts/homelab-ollama-startup.ps1`:

```powershell
ollama pull gemma3:12b
ollama pull qwen3:8b
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Modelos adicionales mencionados por el runtime (descargar solo los necesarios):

```powershell
ollama pull qwen3:4b
ollama pull qwen2.5-coder:7b
ollama pull qwen3-vl:4b
ollama pull phi4-mini
```

Precarga rápida del modelo principal:

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

## 3. Auto-start al boot/logon

En este repo quedó un script listo para correr en el HomeLab:

[`scripts/homelab-ollama-startup.ps1`](../scripts/homelab-ollama-startup.ps1)

Ejecutalo una vez en PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\homelab-ollama-startup.ps1
```

Eso hace:

- configura `OLLAMA_HOST=0.0.0.0:11434`
- levanta `ollama serve` si no está arriba
- hace `pull` de `gemma3:12b`, `qwen3:8b`, `qwen2.5:7b`, `nomic-embed-text` si faltan
- precarga `gemma3:12b`
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

## Diferencia con el router Python

`qwen3-fast:4b` y `qwen3-fast:8b` son modelos custom de `ROLE_MAP`.
El script de startup no los crea: requiere importar o recrear sus Modelfiles.
No asumir que los tags `qwen3:4b`/`qwen3:8b` son equivalentes. Verificar
`ollama list` y una inferencia con el nombre exacto antes de habilitar el flujo.

La escucha en `0.0.0.0:11434` requiere reglas de red que permitan únicamente
los clientes previstos. Estos pasos deben ejecutarse en el HomeLab correspondiente.
