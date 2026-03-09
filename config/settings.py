from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator


load_dotenv()


class GitSettings(BaseModel):
    auto_commit: bool = Field(default=True)
    commit_message_template: str = Field(
        default="chore: update dnd chronicle",
    )


class Settings(BaseModel):
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_ids: List[int] = Field(alias="TELEGRAM_ALLOWED_USER_IDS")

    google_api_key: str = Field(alias="GOOGLE_API_KEY")

    vault_path: Path = Field(alias="VAULT_PATH")

    git: GitSettings = Field(default_factory=GitSettings)

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: str | List[int]) -> List[int]:
        if isinstance(value, list):
            return value
        parts = [p.strip() for p in str(value).split(",") if p.strip()]
        return [int(p) for p in parts]

    @field_validator("vault_path", mode="before")
    @classmethod
    def ensure_path(cls, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        return path

    @field_validator("telegram_bot_token", "google_api_key", mode="after")
    @classmethod
    def not_empty(cls, value: str) -> str:
        if not value:
            msg = "must not be empty"
            raise ValueError(msg)
        return value

    @classmethod
    def load(cls) -> "Settings":
        try:
            return cls(
                TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                TELEGRAM_ALLOWED_USER_IDS=os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""),
                GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY", ""),
                VAULT_PATH=os.getenv("VAULT_PATH", str(Path("vault").resolve())),
                git=GitSettings(
                    auto_commit=os.getenv("GIT_AUTO_COMMIT", "true").lower()
                    in {"1", "true", "yes", "y"},
                    commit_message_template=os.getenv(
                        "GIT_COMMIT_MESSAGE_TEMPLATE",
                        "chore: update dnd chronicle",
                    ),
                ),
            )
        except ValidationError as exc:
            raise RuntimeError(f"Invalid configuration: {exc}") from exc


settings = Settings.load()

