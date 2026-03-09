from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from config.settings import settings
from services.entities import ParsedLog
from services.gemini_client import GeminiError, gemini_client
from services.git_sync import GitPullError, git_sync
from services.vault_manager import vault_manager


def _is_allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in settings.telegram_allowed_user_ids)


async def _ensure_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if _is_allowed(update):
        return True
    if update.effective_chat:
        await update.effective_chat.send_message(
            "У вас нет доступа к этому боту.",
        )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    text = (
        "Привет! Я твой Цифровой Летописец D&D.\n\n"
        "Используй комнаду /log **Текст**" 
        "Отправь сообщение с логом сессии (текстом или голосом), "
        "затем ответь на него командой /log."
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    text = (
        "/log — проанализировать лог сессии.\n"
        "Использование:\n"
        "1. Отправь текст или голосовое сообщение с описанием сессии.\n"
        "2. Ответь на это сообщение командой /log."
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return

    message = update.message
    if not message:
        return

    # Перед обработкой лога подтянуть изменения из репозитория Vault
    try:
        git_sync.pull()
    except GitPullError as exc:
        await message.reply_text(
            "Не удалось выполнить git pull для летописи.\n"
            "Разреши конфликты/настрой Git вручную в своём Vault и попробуй ещё раз.\n\n"
            f"Сообщение git:\n{exc.message}",
        )
        return

    src = message.reply_to_message
    text: str | None = None

    if src and src.voice:
        await message.reply_text("Получил голосовой лог, начинаю транскрибацию...")
        text = await _process_voice(src, context)
    elif src and (src.text or src.caption):
        text = src.text or src.caption
    elif context.args:
        # Поддержка варианта `/log текст лога` в одном сообщении
        text = " ".join(context.args)
    else:
        await message.reply_text(
            "Не нашёл текст лога.\n"
            "Сделай так:\n"
            "1) Отправь сообщение с описанием сессии (или голосовое).\n"
            "2) Ответь на него командой /log\n"
            "или используй `/log текст лога` в одном сообщении.",
        )
        return

    if not text:
        await message.reply_text(
            "В логе нет текста. Отправь текст или голосовое сообщение.",
        )
        return

    await message.reply_text("Обрабатываю лог через Gemini и обновляю Obsidian Vault...")

    try:
        parsed = gemini_client.extract_entities(text)
    except GeminiError as exc:
        # Если Gemini недоступен (например, из-за ограничений по региону),
        # просто сохраняем сырой лог без структурированного разбора.
        await message.reply_text(
            "Не получилось обратиться к Gemini (возможно, ограничение по региону).\n"
            "Я сохраню только сырой лог без разбора NPC/локаций/квестов.\n\n"
            f"Техническая причина:\n{exc}",
        )
        parsed = ParsedLog(raw_text=text)
    else:
        parsed.raw_text = text

    session_path = vault_manager.apply_parsed_log(parsed)
    git_sync.sync("session log")

    await message.reply_text(
        f"Готово! Сессия сохранена: {session_path.name}",
    )


async def _process_voice(
    message,
    context: ContextTypes.DEFAULT_TYPE,
) -> str:
    voice = message.voice
    if not voice:
        return ""

    file = await context.bot.get_file(voice.file_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "voice.ogg"
        await file.download_to_drive(custom_path=str(tmp_path))
        transcript = gemini_client.transcribe_audio(tmp_path)

    return transcript


def main() -> None:
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("log", log_command))

    application.run_polling()


if __name__ == "__main__":
    main()

