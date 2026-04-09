@echo off
if exist C:\llama.cpp\convert_hf_to_gguf.py (
  echo [ok] convert_hf_to_gguf.py encontrado
) else (
  echo [warn] falta C:\llama.cpp\convert_hf_to_gguf.py
)
C:\Python314\python.exe -c "import transformers; print('transformers OK')"
C:\Python314\python.exe -c "import sentencepiece; print('sentencepiece OK')" 2>nul
C:\Python314\python.exe -c "import gguf; print('gguf OK')" 2>nul
pause
