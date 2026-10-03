$ErrorActionPreference = "Stop"

$StartupDir = Join-Path $env:USERPROFILE "tenazas-startup"
$StartupScript = Join-Path $StartupDir "start-ollama.ps1"
$StartupBat = Join-Path $StartupDir "start-ollama.bat"
$StartupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$OllamaExe = "ollama"
$OllamaHost = "0.0.0.0:11434"
$WarmModel = "gemma3:12b"
$Models = @(
    "gemma3:12b",
    "qwen3:8b",
    "qwen2.5:7b",
    "nomic-embed-text"
)

function Test-OllamaEndpoint {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
        return $true
    } catch {
        return $false
    }
}

function Ensure-OllamaServe {
    $env:OLLAMA_HOST = $OllamaHost
    setx OLLAMA_HOST $OllamaHost | Out-Null
    if (-not (Test-OllamaEndpoint)) {
        Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 5
    }
}

function Pull-MissingModels {
    $existing = @()
    try {
        $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 10
        $existing = @($tags.models | ForEach-Object { $_.name })
    } catch {
        $existing = @()
    }

    foreach ($model in $Models) {
        if ($existing -notcontains $model -and $existing -notcontains "$model`:latest") {
            & $OllamaExe pull $model
        }
    }
}

function Warm-Models {
    $body = @{
        model = $WarmModel
        prompt = "ok"
        stream = $false
        keep_alive = "1h"
        options = @{ num_predict = 1 }
    } | ConvertTo-Json -Depth 4
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" `
            -Method Post -ContentType "application/json" -Body $body -TimeoutSec 30 | Out-Null
    } catch {
        Write-Warning "No pude precargar ${WarmModel}: $($_.Exception.Message)"
    }
}

function Install-StartupShortcut {
    New-Item -ItemType Directory -Force -Path $StartupDir | Out-Null

    @'
$ErrorActionPreference = "Stop"
$env:OLLAMA_HOST = "0.0.0.0:11434"
setx OLLAMA_HOST "0.0.0.0:11434" | Out-Null
Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
Start-Sleep -Seconds 5
try {
  $body = @{
    model = "gemma3:12b"
    prompt = "ok"
    stream = $false
    keep_alive = "1h"
    options = @{ num_predict = 1 }
  } | ConvertTo-Json -Depth 4
  Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 30 | Out-Null
} catch {}
'@ | Set-Content -Path $StartupScript -Encoding ASCII

    "@echo off`r`npowershell -ExecutionPolicy Bypass -File `"$StartupScript`"`r`n" |
        Set-Content -Path $StartupBat -Encoding ASCII

    Copy-Item -Force $StartupBat (Join-Path $StartupFolder "start-ollama.bat")
}

Ensure-OllamaServe
Pull-MissingModels
Warm-Models
Install-StartupShortcut

Write-Host "Ollama listo."
Write-Host "Modelos objetivo: $($Models -join ', ')"
Write-Host "Modelo precargado: $WarmModel"
Write-Host "Autostart instalado en: $StartupFolder\start-ollama.bat"
