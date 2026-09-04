"""
FastAPI-шлюз между клиентами (Telegram-бот, веб-интерфейс) и ComfyUI.

Что делает:
1. Принимает запрос с текстовым промптом.
2. (Опционально) улучшает промпт через локальную LLM.
3. Подставляет промпт в шаблон workflow.json и отправляет в ComfyUI.
4. Дожидается завершения генерации и отдаёт готовую картинку.
"""

import os
import json
import time
import uuid
import logging
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from llm_prompter import PromptEnhancer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gateway")

# --------------------------------------------------------------------------- #
# Конфигурация
# --------------------------------------------------------------------------- #
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://comfyui:8188")
WORKFLOW_PATH = os.getenv("DEFAULT_WORKFLOW", "/app/workflows/default_workflow.json")
OUTPUT_DIR = Path("/app/output")
ENABLE_LLM = os.getenv("ENABLE_LLM_PROMPTER", "true").lower() == "true"

DEFAULT_WIDTH = int(os.getenv("DEFAULT_WIDTH", 1024))
DEFAULT_HEIGHT = int(os.getenv("DEFAULT_HEIGHT", 1024))
DEFAULT_STEPS = int(os.getenv("DEFAULT_STEPS", 25))

app = FastAPI(title="Local AI Image Gateway")

# Ленивая инициализация LLM — модель грузится только при первом запросе,
# чтобы контейнер быстро стартовал, даже если модель ещё не скачана.
_enhancer: Optional[PromptEnhancer] = None


def get_enhancer() -> Optional[PromptEnhancer]:
    global _enhancer
    if not ENABLE_LLM:
        return None
    if _enhancer is None:
        _enhancer = PromptEnhancer()
    return _enhancer


# --------------------------------------------------------------------------- #
# Модели данных
# --------------------------------------------------------------------------- #
class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = "low quality, blurry, watermark, text, deformed"
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    steps: int = DEFAULT_STEPS
    use_llm: bool = True
    template: str = "default"  # соответствует шаблонам промптов


class GenerateResponse(BaseModel):
    job_id: str
    final_prompt: str
    image_url: str


# --------------------------------------------------------------------------- #
# Шаблоны промптов (presentations / social / design и т.д.)
# --------------------------------------------------------------------------- #
PROMPT_TEMPLATES = {
    "default": "{prompt}",
    "presentation": "{prompt}, clean minimal style, flat design, presentation slide illustration, high contrast, simple background",
    "social": "{prompt}, vibrant colors, eye-catching, social media post, trending on instagram, high detail",
    "design": "{prompt}, professional graphic design, modern aesthetic, balanced composition, studio lighting",
}


# --------------------------------------------------------------------------- #
# Работа с ComfyUI
# --------------------------------------------------------------------------- #
def load_workflow() -> dict:
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_workflow(prompt: str, negative_prompt: str, width: int, height: int, steps: int) -> dict:
    """Подставляет параметры в шаблон workflow ComfyUI (API-формат)."""
    wf = load_workflow()

    # Эти id узлов соответствуют workflow.json из этого репозитория.
    # Если вы используете свой workflow — поменяйте id под свой граф.
    wf["6"]["inputs"]["text"] = prompt              # положительный промпт (CLIPTextEncode)
    wf["7"]["inputs"]["text"] = negative_prompt      # отрицательный промпт (CLIPTextEncode)
    wf["5"]["inputs"]["width"] = width                # EmptyLatentImage
    wf["5"]["inputs"]["height"] = height
    wf["3"]["inputs"]["steps"] = steps                # KSampler
    wf["3"]["inputs"]["seed"] = int.from_bytes(os.urandom(4), "big")
    return wf


def queue_prompt(workflow: dict, client_id: str) -> str:
    resp = requests.post(
        f"{COMFYUI_URL}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["prompt_id"]


def wait_for_result(prompt_id: str, timeout: int = 300) -> dict:
    """Опрашивает /history пока генерация не завершится."""
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
        resp.raise_for_status()
        history = resp.json()
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(1)
    raise TimeoutError("ComfyUI не ответил за отведённое время")


def extract_image_filename(history_entry: dict) -> str:
    outputs = history_entry.get("outputs", {})
    for node_output in outputs.values():
        images = node_output.get("images", [])
        if images:
            return images[0]["filename"]
    raise RuntimeError("В результате генерации не найдено изображение")


def fetch_image(filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
    resp = requests.get(
        f"{COMFYUI_URL}/view",
        params={"filename": filename, "subfolder": subfolder, "type": folder_type},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


# --------------------------------------------------------------------------- #
# Эндпоинты
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        requests.get(f"{COMFYUI_URL}/system_stats", timeout=5).raise_for_status()
        comfy_ok = True
    except Exception:
        comfy_ok = False
    return {"status": "ok", "comfyui_reachable": comfy_ok}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    final_prompt = PROMPT_TEMPLATES.get(req.template, "{prompt}").format(prompt=req.prompt)

    if req.use_llm:
        enhancer = get_enhancer()
        if enhancer is not None:
            try:
                final_prompt = enhancer.enhance(final_prompt)
            except Exception as e:
                log.warning(f"LLM enhance failed, using original prompt: {e}")

    workflow = build_workflow(final_prompt, req.negative_prompt, req.width, req.height, req.steps)
    client_id = str(uuid.uuid4())

    try:
        prompt_id = queue_prompt(workflow, client_id)
        result = wait_for_result(prompt_id)
        filename = extract_image_filename(result)
        image_bytes = fetch_image(filename)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Не удалось связаться с ComfyUI: {e}")
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    job_id = str(uuid.uuid4())
    out_path = OUTPUT_DIR / f"{job_id}.png"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(image_bytes)

    return GenerateResponse(
        job_id=job_id,
        final_prompt=final_prompt,
        image_url=f"/image/{job_id}",
    )


@app.get("/image/{job_id}")
def get_image(job_id: str):
    path = OUTPUT_DIR / f"{job_id}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Изображение не найдено")
    return FileResponse(path, media_type="image/png")


@app.get("/templates")
def list_templates():
    return {"templates": list(PROMPT_TEMPLATES.keys())}
