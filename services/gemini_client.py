from __future__ import annotations

from pathlib import Path
from typing import Any

import google.generativeai as genai

from config.settings import settings
from services.entities import ParsedLog


genai.configure(api_key=settings.google_api_key)


TEXT_MODEL_NAME = "gemini-3.1-flash-lite-preview"
AUDIO_MODEL_NAME = "gemini-3.1-flash-lite-preview"


class GeminiError(Exception):
    """Generic Gemini API error."""


class GeminiClient:
    def __init__(
        self,
        text_model_name: str = TEXT_MODEL_NAME,
        audio_model_name: str = AUDIO_MODEL_NAME,
    ) -> None:
        self._text_model = genai.GenerativeModel(text_model_name)
        self._audio_model = genai.GenerativeModel(audio_model_name)

    def transcribe_audio(self, file_path: Path) -> str:
        audio_bytes = file_path.read_bytes()
        prompt = (
            "You are a transcription assistant. "
            "Transcribe the following tabletop RPG session audio to Russian text. "
            "Return only the transcript, without extra comments."
        )
        try:
            result = self._audio_model.generate_content(
                [
                    prompt,
                    {
                        "mime_type": "audio/ogg",
                        "data": audio_bytes,
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(str(exc)) from exc

        return (result.text or "").strip()

    def extract_entities(self, text: str) -> ParsedLog:
        system_prompt = (
            "You are an assistant that analyzes Dungeons & Dragons session logs "
            "and extracts structured entities.\n\n"
            "Return STRICTLY valid JSON in UTF-8 without markdown fences. "
            "Schema:\n"
            "{\n"
            '  \"session_date\": string | null,\n'
            '  \"npcs\": [ { \"name\": string, \"description\": string | null, '
            '\"status\": string | null, \"tags\": [string] | null, '
            '\"links_to_locations\": [string] | null } ],\n'
            '  \"locations\": [ { \"name\": string, \"description\": string | null, '
            '\"status\": string | null, \"region\": string | null } ],\n'
            '  \"quests\": [ { \"name\": string, \"summary\": string | null, '
            '\"status\": string | null, \"related_npcs\": [string] | null, '
            '\"related_locations\": [string] | null } ],\n'
            '  \"items\": [ { \"name\": string, \"description\": string | null, '
            '\"owner\": string | null, \"status\": string | null } ]\n'
            "}\n\n"
            "session_date can be approximated from the text or set to null.\n"
            "Use Russian field values where appropriate."
        )

        try:
            result = self._text_model.generate_content(
                [
                    system_prompt,
                    "Session log:\n",
                    text,
                ],
                generation_config={
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(str(exc)) from exc

        raw_json = (result.text or "{}").strip()

        # На практике модель иногда всё равно оборачивает JSON в ``` или ```json.
        # Срежем markdown-ограждения как в начале, так и в конце, если они есть.
        lines = raw_json.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].lstrip().startswith("```"):
            lines = lines[:-1]
        raw_json = "\n".join(lines).strip()

        try:
            data: Any = ParsedLog.model_validate_json(raw_json)
        except Exception as exc:  # noqa: BLE001
            # Пробрасываем более понятную ошибку с кусочком ответа модели.
            snippet = raw_json[:500]
            raise GeminiError(
                f"Failed to parse Gemini JSON: {exc}\n\nSnippet:\n{snippet}",
            ) from exc

        if isinstance(data, ParsedLog):
            return data
        # model_validate_json already returns ParsedLog, but keep fallback
        return ParsedLog.model_validate(data)

    def summarize_campaign(self, text: str) -> str:
        if not text.strip():
            return ""

        system_prompt = (
            "Ты выступаешь в роли летописца Dungeons & Dragons.\n"
            "Тебе дают выдержки из летописи кампании (логи сессий, заметки и т.п.).\n"
            "На их основе СЖАТО перескажи историю, которую прожили главные герои.\n\n"
            "Формат ответа:\n"
            "- Пиши на русском языке.\n"
            "- Сделай связный пересказ без списков.\n"
            "- 3–6 абзацев, максимум 1500–2000 символов.\n"
            "- Сфокусируйся на арках персонажей, ключевых поворотах сюжета и текущем статусе дел.\n"
            "- Не упоминай технические детали наподобие файлов, ссылок Obsidian или разметки.\n"
        )

        try:
            result = self._text_model.generate_content(
                [
                    system_prompt,
                    "Фрагменты летописи кампании:\n",
                    text,
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(str(exc)) from exc

        return (result.text or "").strip()


gemini_client = GeminiClient()

