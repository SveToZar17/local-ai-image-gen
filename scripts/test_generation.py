"""
Простой тест: проверяет, что шлюз и ComfyUI работают и могут сгенерировать картинку.

Использование:
    python scripts/test_generation.py
    python scripts/test_generation.py --prompt "кот-космонавт" --no-llm
"""

import argparse
import sys
import time

import requests

API_BASE_URL = "http://localhost:8000"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="a red fox sitting in an autumn forest, cinematic lighting")
    parser.add_argument("--no-llm", action="store_true", help="Не использовать LLM для улучшения промпта")
    parser.add_argument("--template", default="default")
    args = parser.parse_args()

    print(f"1) Проверяю здоровье сервиса ({API_BASE_URL}/health)...")
    resp = requests.get(f"{API_BASE_URL}/health", timeout=10)
    resp.raise_for_status()
    health = resp.json()
    print(f"   Статус: {health}")
    if not health.get("comfyui_reachable"):
        print("   ⚠️  ComfyUI недоступен. Проверьте, что контейнер comfyui запущен и здоров.")
        sys.exit(1)

    print(f"\n2) Отправляю запрос на генерацию: '{args.prompt}'")
    start = time.time()
    resp = requests.post(
        f"{API_BASE_URL}/generate",
        json={
            "prompt": args.prompt,
            "template": args.template,
            "use_llm": not args.no_llm,
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    elapsed = time.time() - start

    print(f"   Итоговый промпт: {data['final_prompt']}")
    print(f"   Время генерации: {elapsed:.1f} сек")

    print(f"\n3) Скачиваю результат ({data['image_url']})...")
    img_resp = requests.get(f"{API_BASE_URL}{data['image_url']}", timeout=60)
    img_resp.raise_for_status()

    out_file = "test_result.png"
    with open(out_file, "wb") as f:
        f.write(img_resp.content)

    print(f"\n✅ Готово! Картинка сохранена в {out_file}")


if __name__ == "__main__":
    main()
