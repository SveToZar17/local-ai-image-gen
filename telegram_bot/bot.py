"""
Telegram-бот: принимает текстовый промпт от пользователя,
отправляет в FastAPI-шлюз, возвращает готовую картинку.
"""

import os
import logging

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import Conflict

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telegram_bot")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
ALLOWED_TELEGRAM_CHAT_IDS = {
    int(value.strip())
    for value in os.getenv("ALLOWED_TELEGRAM_CHAT_IDS", "").split(",")
    if value.strip()
}

TEMPLATES = {
    "default": "Обычный",
    "presentation": "Для презентаций",
    "social": "Для соцсетей",
    "design": "Дизайн",
}

# Храним выбранный шаблон и промпт пользователя между сообщением и нажатием кнопки
user_state: dict[int, dict] = {}


def is_allowed(update: Update) -> bool:
    if not ALLOWED_TELEGRAM_CHAT_IDS:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id in ALLOWED_TELEGRAM_CHAT_IDS


def deny_if_not_allowed(update: Update) -> bool:
    return not is_allowed(update)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if deny_if_not_allowed(update):
        return
    await update.message.reply_text(
        "Привет! Я генерирую картинки на локальной видеокарте хозяина этого бота 🖼\n\n"
        "Просто отправь мне текстовое описание картинки, и я её сгенерирую.\n"
        "Команды:\n"
        "/template — выбрать стиль промпта (презентация, соцсети, дизайн)\n"
        "/help — помощь"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if deny_if_not_allowed(update):
        return
    await update.message.reply_text(
        "Просто напишите, что нарисовать, например:\n"
        "«рыжий кот в скафандре на луне»\n\n"
        "Я улучшу промпт (если включена LLM) и сгенерирую картинку локально, "
        "без облаков и подписок."
    )


async def choose_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if deny_if_not_allowed(update):
        return
    buttons = [
        [InlineKeyboardButton(label, callback_data=key)]
        for key, label in TEMPLATES.items()
    ]
    await update.message.reply_text(
        "Выберите шаблон промпта:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_template_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if deny_if_not_allowed(update):
        await update.callback_query.answer("Доступ запрещён", show_alert=True)
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_state.setdefault(chat_id, {})["template"] = query.data
    await query.edit_message_text(f"Шаблон выбран: {TEMPLATES[query.data]}")


async def on_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if deny_if_not_allowed(update):
        return
    chat_id = update.effective_chat.id
    prompt = update.message.text.strip()
    template = user_state.get(chat_id, {}).get("template", "default")

    status_msg = await update.message.reply_text("🎨 Генерирую картинку, это может занять минуту...")

    try:
        resp = requests.post(
            f"{API_BASE_URL}/generate",
            json={"prompt": prompt, "template": template, "use_llm": True},
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()

        image_resp = requests.get(f"{API_BASE_URL}{data['image_url']}", timeout=60)
        image_resp.raise_for_status()

        caption = f"Готово ✅\nИтоговый промпт: {data['final_prompt'][:900]}"
        await update.message.reply_photo(
            photo=image_resp.content,
            caption=caption,
        )
    except requests.exceptions.RequestException as e:
        log.exception("Ошибка при обращении к API")
        await update.message.reply_text(f"⚠️ Не получилось сгенерировать картинку: {e}")
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("template", choose_template))
    app.add_handler(CallbackQueryHandler(on_template_selected))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_prompt))

    log.info("Бот запущен, жду сообщений...")
    try:
        app.run_polling()
    except Conflict:
        log.error(
            "Конфликт: другой экземпляр бота уже запущен (возможно, в другом контейнере). "
            "Остановите дубликат: docker compose stop telegram_bot"
        )
        raise


if __name__ == "__main__":
    main()
