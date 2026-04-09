$hfHome = "D:\hf_cache"
$hubDir = "D:\hf_cache\hub"

New-Item -ItemType Directory -Force -Path $hfHome | Out-Null
New-Item -ItemType Directory -Force -Path $hubDir | Out-Null

[Environment]::SetEnvironmentVariable("HF_HOME", $hfHome, "Machine")
[Environment]::SetEnvironmentVariable("TRANSFORMERS_CACHE", $hubDir, "Machine")

Write-Host "[ok] HF_HOME =" ([Environment]::GetEnvironmentVariable("HF_HOME", "Machine"))
Write-Host "[ok] TRANSFORMERS_CACHE =" ([Environment]::GetEnvironmentVariable("TRANSFORMERS_CACHE", "Machine"))
