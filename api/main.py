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
import threading

from contextlib import nullcontext
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
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
MAX_WIDTH = int(os.getenv("MAX_WIDTH", 1536))
MAX_HEIGHT = int(os.getenv("MAX_HEIGHT", 1536))
MAX_STEPS = int(os.getenv("MAX_STEPS", 50))
GENERATION_CONCURRENCY = max(1, int(os.getenv("GENERATION_CONCURRENCY", 1)))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",")
    if origin.strip()
]

app = FastAPI(title="Local AI Image Gateway")
_generation_semaphore = threading.BoundedSemaphore(GENERATION_CONCURRENCY)

# CORS — чтобы веб-интерфейс с другого порта мог обращаться к API
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------- #
# LLM — thread-safe ленивая инициализация
# --------------------------------------------------------------------------- #
_enhancer: Optional[PromptEnhancer] = None
_enhancer_lock = threading.Lock()


def get_enhancer() -> Optional[PromptEnhancer]:
    global _enhancer
    if not ENABLE_LLM:
        return None
    if _enhancer is None:
        with _enhancer_lock:
            # Double-checked locking — защита от race condition
            if _enhancer is None:
                _enhancer = PromptEnhancer()
    return _enhancer


# --------------------------------------------------------------------------- #
# Workflow — загружаем один раз при старте приложения
# --------------------------------------------------------------------------- #
_workflow_template: Optional[dict] = None


@app.on_event("startup")
def startup_event():
    global _workflow_template
    _workflow_template = load_workflow()
    log.info("Workflow template loaded from %s", WORKFLOW_PATH)


def load_workflow() -> dict:
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_workflow(prompt: str, negative_prompt: str, width: int, height: int, steps: int) -> dict:
    """Подставляет параметры в шаблон workflow ComfyUI (API-формат)."""
    if _workflow_template is None:
        raise RuntimeError("Workflow template not loaded")

    wf = json.loads(json.dumps(_workflow_template))  # deep copy

    # Безопасная подстановка по ID (для стандартного workflow из репозитория)
    if "6" in wf and isinstance(wf["6"], dict) and "inputs" in wf["6"]:
        wf["6"]["inputs"]["text"] = prompt              # положительный промпт (CLIPTextEncode)
    if "7" in wf and isinstance(wf["7"], dict) and "inputs" in wf["7"]:
        wf["7"]["inputs"]["text"] = negative_prompt      # отрицательный промпт (CLIPTextEncode)
    if "5" in wf and isinstance(wf["5"], dict) and "inputs" in wf["5"]:
        wf["5"]["inputs"]["width"] = width                # EmptyLatentImage
        wf["5"]["inputs"]["height"] = height
    if "3" in wf and isinstance(wf["3"], dict) and "inputs" in wf["3"]:
        wf["3"]["inputs"]["steps"] = steps                # KSampler
        wf["3"]["inputs"]["seed"] = int.from_bytes(os.urandom(4), "big")
    return wf


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
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Промпт не может быть пустым")
    if req.width < 64 or req.width > MAX_WIDTH or req.height < 64 or req.height > MAX_HEIGHT:
        raise HTTPException(status_code=400, detail=f"Размер должен быть от 64 до {MAX_WIDTH}x{MAX_HEIGHT}")
    if req.steps < 1 or req.steps > MAX_STEPS:
        raise HTTPException(status_code=400, detail=f"Количество шагов должно быть от 1 до {MAX_STEPS}")

    # Используем replace вместо format — защита от фигурных скобок в промпте пользователя
    template = PROMPT_TEMPLATES.get(req.template, "{prompt}")
    final_prompt = template.replace("{prompt}", req.prompt)

    if req.use_llm:
        enhancer = get_enhancer()
        if enhancer is not None:
            try:
                final_prompt = enhancer.enhance(final_prompt)
            except Exception as e:
                log.warning("LLM enhance failed, using original prompt: %s", e)

    workflow = build_workflow(final_prompt, req.negative_prompt, req.width, req.height, req.steps)
    client_id = str(uuid.uuid4())

    acquired = _generation_semaphore.acquire(timeout=1)
    if not acquired:
        raise HTTPException(status_code=429, detail="Генератор занят. Попробуйте ещё раз через несколько секунд.")

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
    finally:
        _generation_semaphore.release()

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
