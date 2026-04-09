@echo off
setlocal enabledelayedexpansion

set "MODEL=%~1"
set "DATASET=%~2"
set "OUTPUT=%~3"
set "EPOCHS=%~4"
set "BATCH=%~5"

if "%MODEL%"=="" goto :usage
if "%DATASET%"=="" goto :usage
if "%OUTPUT%"=="" goto :usage
if "%EPOCHS%"=="" set "EPOCHS=3"
if "%BATCH%"=="" set "BATCH=4"

set "PYTHON=C:\Python314\python.exe"
set "WORKDIR=C:\Users\sbasadre\tool_tuning"
set "LORA_DIR=%WORKDIR%\%OUTPUT%-lora"
set "MERGED_DIR=%WORKDIR%\%OUTPUT%-merged"
set "GGUF_PATH=%WORKDIR%\%OUTPUT%.gguf"
set "MODELFILE=%WORKDIR%\Modelfile.%OUTPUT%"
set "LOGDIR=%WORKDIR%\logs"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%WORKDIR%"

echo ============================================
echo   Tenazas Training Pipeline
echo   Model:   %MODEL%
echo   Dataset: %DATASET%
echo   Output:  %OUTPUT%
echo   Epochs:  %EPOCHS%
echo   Batch:   %BATCH%
echo ============================================

echo [1/5] Entrenando LoRA...
%PYTHON% train_multitool.py ^
  --model "%MODEL%" ^
  --dataset "%DATASET%" ^
  --output "%LORA_DIR%" ^
  --epochs %EPOCHS% ^
  --batch-size %BATCH% ^
  --lr 2e-4 ^
  --gradient-accumulation 4 > "%LOGDIR%\%OUTPUT%_train.log" 2>&1
if errorlevel 1 (
  echo [error] Fallo training. Ver log: "%LOGDIR%\%OUTPUT%_train.log"
  exit /b 1
)

echo [2/5] Mergeando adapter...
%PYTHON% -c "from peft import PeftModel; from transformers import AutoModelForCausalLM, AutoTokenizer; import torch; base=AutoModelForCausalLM.from_pretrained(r'%MODEL%', torch_dtype=torch.float16, device_map='cpu'); tok=AutoTokenizer.from_pretrained(r'%MODEL%'); m=PeftModel.from_pretrained(base, r'%LORA_DIR%'); merged=m.merge_and_unload(); merged.save_pretrained(r'%MERGED_DIR%'); tok.save_pretrained(r'%MERGED_DIR%'); print('Merge OK')" > "%LOGDIR%\%OUTPUT%_merge.log" 2>&1
if errorlevel 1 (
  echo [error] Fallo merge. Ver log: "%LOGDIR%\%OUTPUT%_merge.log"
  exit /b 1
)

if not exist "%MERGED_DIR%\config.json" (
  echo [error] Merge incompleto: no existe "%MERGED_DIR%\config.json"
  exit /b 1
)

echo [3/5] Convirtiendo a GGUF...
call "%WORKDIR%\convert_to_gguf.bat" "%MERGED_DIR%" "%WORKDIR%\%OUTPUT%" > "%LOGDIR%\%OUTPUT%_gguf.log" 2>&1
if errorlevel 1 (
  echo [error] Fallo conversion a GGUF. Ver log: "%LOGDIR%\%OUTPUT%_gguf.log"
  exit /b 1
)

if not exist "%GGUF_PATH%" (
  echo [error] No se genero GGUF: "%GGUF_PATH%"
  exit /b 1
)

echo [4/5] Creando modelo Ollama desde GGUF...
(
echo FROM %GGUF_PATH%
echo PARAMETER temperature 0
echo PARAMETER num_ctx 4096
echo TEMPLATE """{{ .Prompt }}"""
) > "%MODELFILE%"

ollama create "%OUTPUT%" -f "%MODELFILE%" > "%LOGDIR%\%OUTPUT%_ollama_create.log" 2>&1
if errorlevel 1 (
  echo [error] Fallo ollama create. Ver log: "%LOGDIR%\%OUTPUT%_ollama_create.log"
  exit /b 1
)

echo [5/5] Test rapido...
ollama run "%OUTPUT%" "hola" > "%LOGDIR%\%OUTPUT%_test.log" 2>&1
if errorlevel 1 (
  echo [error] Fallo test rapido. Ver log: "%LOGDIR%\%OUTPUT%_test.log"
  exit /b 1
)

echo ============================================
echo   Pipeline completado: %OUTPUT%
echo   GGUF: %GGUF_PATH%
echo ============================================
exit /b 0

:usage
echo Uso: full_pipeline.bat ^<model_name^> ^<dataset^> ^<output_name^> [epochs] [batch]
echo Ejemplo: full_pipeline.bat Qwen/Qwen2.5-3B-Instruct multitool_train.jsonl tenazas-tools-3b 5 8
exit /b 1
