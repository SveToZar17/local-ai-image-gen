#!/usr/bin/env bash
#
# Скачивает квантованную LLM (Llama 3.2 3B Instruct, GGUF) для улучшения промптов.
# Модель работает на CPU и не занимает видеопамять.
#
# Использование: ./scripts/download_llm.sh
#
set -e

LLM_DIR="$(dirname "$0")/../llm_models"
mkdir -p "$LLM_DIR"

URL="https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
FILENAME="llama-3.2-3b-instruct-q4_k_m.gguf"

echo "Скачиваю Llama 3.2 3B Instruct (Q4_K_M, ~2 ГБ)..."
curl -L --progress-bar -o "$LLM_DIR/$FILENAME" "$URL"

echo ""
echo "Готово: $LLM_DIR/$FILENAME"
echo "Убедитесь, что в .env указано:"
echo "  ENABLE_LLM_PROMPTER=true"
echo "  LLM_MODEL_PATH=/app/llm_models/$FILENAME"
