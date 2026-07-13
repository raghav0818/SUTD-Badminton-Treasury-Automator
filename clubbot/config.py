"""Configuration from .env / environment variables."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    treasurer_id: int
    db_path: str
    gemini_api_key: str
    gemini_model: str
    google_credentials: str
    sheet_id: str


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("BOT_TOKEN", "")
    treasurer = os.environ.get("TREASURER_TELEGRAM_ID", "")
    if not token:
        raise SystemExit("BOT_TOKEN is missing - copy .env.example to .env and fill it in.")
    if not treasurer.isdigit():
        raise SystemExit("TREASURER_TELEGRAM_ID is missing or not a number in .env.")
    return Config(
        bot_token=token,
        treasurer_id=int(treasurer),
        db_path=os.environ.get("DB_PATH", "clubbot.db"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        google_credentials=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
        sheet_id=os.environ.get("SHEET_ID", ""),
    )
