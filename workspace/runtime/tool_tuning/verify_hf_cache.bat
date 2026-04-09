@echo off
echo HF_HOME=%HF_HOME%
echo TRANSFORMERS_CACHE=%TRANSFORMERS_CACHE%
if exist D:\hf_cache (
  echo [ok] D:\hf_cache existe
) else (
  echo [warn] D:\hf_cache no existe
)
pause
