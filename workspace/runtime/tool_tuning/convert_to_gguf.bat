@echo off
setlocal enabledelayedexpansion

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

set "MERGED_DIR=%~1"
set "OUTPUT_NAME=%~2"
set "PYTHON=C:\Python314\python.exe"
set "CONVERTER=C:\llama.cpp\convert_hf_to_gguf.py"

if not exist "%MERGED_DIR%" (
  echo [error] No existe el directorio mergeado: "%MERGED_DIR%"
  exit /b 1
)

if not exist "%CONVERTER%" (
  echo [error] Falta el conversor oficial: "%CONVERTER%"
  echo [hint] Descargar llama.cpp para Windows y ubicarlo en C:\llama.cpp\
  exit /b 1
)

echo [convert] Convirtiendo "%MERGED_DIR%" a "%OUTPUT_NAME%.gguf" ...
%PYTHON% "%CONVERTER%" "%MERGED_DIR%" --outfile "%OUTPUT_NAME%.gguf" --outtype q4_k_m
if errorlevel 1 (
  echo [error] Fallo la conversion a GGUF
  exit /b 1
)

if not exist "%OUTPUT_NAME%.gguf" (
  echo [error] El archivo GGUF no fue generado
  exit /b 1
)

echo [ok] GGUF generado: "%OUTPUT_NAME%.gguf"
exit /b 0

:usage
echo Uso: convert_to_gguf.bat ^<directorio_merged^> ^<nombre_output^>
echo Ejemplo: convert_to_gguf.bat tenazas-tools-3b-merged tenazas-tools-3b
exit /b 1
