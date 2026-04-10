@echo off
setlocal enabledelayedexpansion

:: ============================================
::   Tenazas Tools v3 — Full Training Pipeline
::   Model: Qwen2.5-3B-Instruct
::   Target: Deterministic XML tool classifier
:: ============================================

set "PYTHON=py -3.11"
set "WORKDIR=C:\Users\sbasadre\tool_tuning"
set "NAS_SRC=\\nas\docker\openclaw\workspace\runtime\tool_tuning"

set "BASE_MODEL=Qwen/Qwen2.5-3B-Instruct"
set "LORA_DIR=%WORKDIR%\tenazas-tools-3b-lora-v3"
set "MERGED_DIR=%WORKDIR%\tenazas-tools-3b-merged-v3"
set "GGUF_PATH=%WORKDIR%\tenazas-tools-3b-v3.gguf"
set "MODELFILE=%WORKDIR%\Modelfile_v3"
set "LOGDIR=%WORKDIR%\logs"

set "TRAIN_DATA=%WORKDIR%\multitool_train_v3.jsonl"
set "EVAL_DATA=%WORKDIR%\multitool_eval_v3.jsonl"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo ============================================
echo   Tenazas Tools v3 Pipeline
echo   Base:    %BASE_MODEL%
echo   Train:   %TRAIN_DATA%
echo   Eval:    %EVAL_DATA%
echo   Output:  tenazas-tools-3b-v3
echo ============================================

:: --- Step 0: Copy files from NAS ---
echo [0/7] Copying files from NAS...
if not exist "%WORKDIR%" mkdir "%WORKDIR%"
copy /Y "%NAS_SRC%\multitool_train_v3.jsonl" "%WORKDIR%\" >nul
copy /Y "%NAS_SRC%\multitool_eval_v3.jsonl" "%WORKDIR%\" >nul
copy /Y "%NAS_SRC%\train_3b.py" "%WORKDIR%\" >nul
copy /Y "%NAS_SRC%\merge_3b.py" "%WORKDIR%\" >nul
copy /Y "%NAS_SRC%\convert_to_gguf.py" "%WORKDIR%\" >nul
copy /Y "%NAS_SRC%\Modelfile_v3" "%WORKDIR%\" >nul
echo [ok] Files copied.

:: --- Step 1: Validate data ---
echo [1/7] Validating datasets...
%PYTHON% -c "import json,re,sys;VALID={'weather','email','calendar','infra-monitor','network-scanner','arr-monitor','self-evolve','reminders','smart-home-on','smart-home-off','smart-home-cinema','tts'};bad=set();[bad.update(set(re.findall(r'<tool>([^<]+)</tool>',l))-VALID) for f in [r'%TRAIN_DATA%',r'%EVAL_DATA%'] for l in open(f)];sys.exit(1) if bad else print(f'[ok] All tools valid')"
if errorlevel 1 (
  echo [error] Invalid tool names found in dataset!
  exit /b 1
)

:: --- Step 2: Train LoRA ---
cd "%WORKDIR%"
echo [2/7] Training LoRA adapter...
%PYTHON% train_3b.py ^
  --base-model "%BASE_MODEL%" ^
  --train "%TRAIN_DATA%" ^
  --eval "%EVAL_DATA%" ^
  --output "%LORA_DIR%" ^
  --epochs 5 ^
  --batch-size 8 ^
  --lr 2e-4 ^
  --lora-r 16 ^
  --lora-alpha 32 ^
  --max-seq-len 256 ^
  --gradient-accumulation 2 > "%LOGDIR%\v3_train.log" 2>&1
if errorlevel 1 (
  echo [error] Training failed. See: "%LOGDIR%\v3_train.log"
  type "%LOGDIR%\v3_train.log" | findstr /i "error missing"
  exit /b 1
)
echo [ok] LoRA saved to %LORA_DIR%

:: --- Step 3: Merge adapter ---
echo [3/7] Merging LoRA into base model...
%PYTHON% merge_3b.py ^
  --base "%BASE_MODEL%" ^
  --lora "%LORA_DIR%" ^
  --output "%MERGED_DIR%" ^
  --device cpu > "%LOGDIR%\v3_merge.log" 2>&1
if errorlevel 1 (
  echo [error] Merge failed. See: "%LOGDIR%\v3_merge.log"
  exit /b 1
)
if not exist "%MERGED_DIR%\config.json" (
  echo [error] Merge incomplete: no config.json
  exit /b 1
)
echo [ok] Merged to %MERGED_DIR%

:: --- Step 4: Convert to GGUF ---
echo [4/7] Converting to GGUF (q8_0)...
%PYTHON% convert_to_gguf.py ^
  --merged "%MERGED_DIR%" ^
  --output "%GGUF_PATH%" ^
  --quantize q8_0 > "%LOGDIR%\v3_gguf.log" 2>&1
if errorlevel 1 (
  echo [error] GGUF conversion failed. See: "%LOGDIR%\v3_gguf.log"
  exit /b 1
)
if not exist "%GGUF_PATH%" (
  echo [error] GGUF not generated: %GGUF_PATH%
  exit /b 1
)
echo [ok] GGUF: %GGUF_PATH%

:: --- Step 5: Create Ollama model ---
echo [5/7] Creating Ollama model...
ollama rm tenazas-multitool 2>nul
ollama create tenazas-multitool -f "%MODELFILE%" > "%LOGDIR%\v3_ollama.log" 2>&1
if errorlevel 1 (
  echo [error] ollama create failed. See: "%LOGDIR%\v3_ollama.log"
  exit /b 1
)
echo [ok] Model: tenazas-multitool

:: --- Step 6: Smoke test ---
echo [6/7] Running smoke tests...
echo.
echo Test 1: "clima y agenda"
ollama run tenazas-multitool "clima y agenda"
echo.
echo Test 2: "prende las luces"
ollama run tenazas-multitool "prende las luces"
echo.
echo Test 3: "buenos dias" (should be empty tools)
ollama run tenazas-multitool "buenos dias"
echo.

:: --- Step 7: Copy GGUF back to NAS ---
echo [7/7] Copying GGUF to NAS...
copy /Y "%GGUF_PATH%" "%NAS_SRC%\" >nul
copy /Y "%LORA_DIR%\training_metrics.json" "%NAS_SRC%\training_metrics_v3.json" >nul 2>nul
echo [ok] Copied to NAS.

:: --- Step 8: Git commit datasets ---
echo [8] Committing datasets to git...
pushd \\nas\docker\openclaw\workspace\runtime\tool_tuning
if errorlevel 1 (
  echo [warn] No se pudo entrar al repo git del NAS
  goto end
)
git status >nul 2>&1
if errorlevel 1 (
  echo [warn] La carpeta no es un repo git
  popd
  goto end
)
git add multitool_dataset_v3.jsonl multitool_train_v3.jsonl multitool_eval_v3.jsonl hard_negatives_v2.jsonl
git commit -m "dataset: pipeline run %date% %time%"
if errorlevel 1 echo [warn] Commit no realizado (sin cambios o error)
popd

:end
echo.
echo ============================================
echo   Pipeline COMPLETE: tenazas-multitool
echo   GGUF: %GGUF_PATH%
echo   Test: ollama run tenazas-multitool "clima"
echo ============================================
exit /b 0
