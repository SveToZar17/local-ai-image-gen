#!/usr/bin/env bash
#
# Скачивает модели Stable Diffusion в папку models/checkpoints
# Использование: ./scripts/download_models.sh
#
set -e

MODELS_DIR="$(dirname "$0")/../models/checkpoints"
mkdir -p "$MODELS_DIR"

echo "=========================================="
echo " Скачивание моделей для генерации картинок"
echo "=========================================="
echo ""
echo "Выберите модель по уровню VRAM вашей видеокарты:"
echo ""
echo "  1) SD 1.5 (~2 ГБ)         — подходит для 4-6 ГБ VRAM"
echo "  2) SDXL Base 1.0 (~7 ГБ)  — подходит для 8-12 ГБ VRAM"
echo "  3) SDXL Turbo (~7 ГБ)     — быстрая генерация, 8-12 ГБ VRAM"
echo "  4) Flux.1-schnell (~23 ГБ)— для 16+ ГБ VRAM (лучшее качество)"
echo "  5) Скачать всё"
echo ""
read -p "Введите номер (1-5): " CHOICE

download() {
  local url="$1"
  local name="$2"
  echo "Скачиваю $name..."
  curl -L --progress-bar -o "$MODELS_DIR/$name" "$url"
  echo "Готово: $MODELS_DIR/$name"
}

case "$CHOICE" in
  1)
    download "https://huggingface.co/runwayml/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors" "sd15-pruned-emaonly.safetensors"
    ;;
  2)
    download "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" "sdxl-base-1.0.safetensors"
    ;;
  3)
    download "https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors" "sdxl-turbo-fp16.safetensors"
    ;;
  4)
    download "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors" "flux1-schnell.safetensors"
    ;;
  5)
    download "https://huggingface.co/runwayml/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors" "sd15-pruned-emaonly.safetensors"
    download "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" "sdxl-base-1.0.safetensors"
    download "https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors" "sdxl-turbo-fp16.safetensors"
    ;;
  *)
    echo "Неверный выбор. Запустите скрипт снова."
    exit 1
    ;;
esac

echo ""
echo "Не забудьте прописать имя файла модели в workflows/default_workflow.json"
echo "(поле ckpt_name узла CheckpointLoaderSimple) или выбрать её в веб-интерфейсе ComfyUI."
