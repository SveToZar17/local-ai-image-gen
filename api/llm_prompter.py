"""
Улучшение промптов пользователя через локальную LLM (Llama 3.2 3B, GGUF).

Работает на CPU через llama-cpp-python, чтобы не занимать VRAM,
который нужен ComfyUI для генерации картинок.

Если файл модели не найден или ENABLE_LLM_PROMPTER=false — модуль просто
не используется, и в шлюзе применяется исходный промпт пользователя.
"""

import os
import logging
from pathlib import Path

log = logging.getLogger("llm_prompter")

MODEL_PATH = os.getenv("LLM_MODEL_PATH", "/app/llm_models/llama-3.2-3b-instruct-q4_k_m.gguf")
LLM_THREADS = int(os.getenv("LLM_THREADS", 6))

SYSTEM_PROMPT = (
    "You are a prompt engineer for a text-to-image diffusion model. "
    "Rewrite the user's idea into a single, detailed, comma-separated English prompt "
    "for image generation. Add relevant details about lighting, composition, style and quality, "
    "but keep the user's original subject and intent intact. "
    "Reply with ONLY the improved prompt, no explanations, no quotes."
)


class PromptEnhancer:
    def __init__(self):
        if not Path(MODEL_PATH).exists():
            raise FileNotFoundError(
                f"LLM модель не найдена по пути {MODEL_PATH}. "
                f"Скачайте её через scripts/download_llm.sh или отключите "
                f"ENABLE_LLM_PROMPTER=false в .env"
            )

        # Импорт внутри __init__, чтобы контейнер стартовал даже если
        # llama-cpp-python не установлен, а LLM отключена.
        from llama_cpp import Llama

        log.info(f"Загружаю LLM из {MODEL_PATH} (CPU, {LLM_THREADS} потоков)...")
        self.llm = Llama(
            model_path=MODEL_PATH,
            n_ctx=2048,
            n_threads=LLM_THREADS,
            n_gpu_layers=0,  # принудительно CPU, VRAM отдаём ComfyUI
            verbose=False,
        )
        log.info("LLM загружена.")

    def enhance(self, user_prompt: str, max_tokens: int = 200) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        result = self.llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.7,
        )
        text = result["choices"][0]["message"]["content"].strip()
        # Небольшая подстраховка на случай, если модель вернёт пустую строку
        return text if text else user_prompt
