"""Pre-launch connectivity check: verifies .env secrets actually work.

Run:  python scripts/preflight.py
Checks the Telegram bot token, Gemini API key, and Google Sheet mirror
without starting the bot or touching clubbot.db. Safe to run any time.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clubbot import config, db  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def report(name: str, status: str, detail: str) -> bool:
    print(f"[{status}] {name}: {detail}")
    return status != FAIL


def describe(exc: Exception) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def check_telegram(cfg) -> bool:
    from telegram import Bot

    try:
        me = asyncio.run(Bot(cfg.bot_token).get_me())
    except Exception as exc:
        return report("Telegram", FAIL, f"bot token rejected ({describe(exc)})")
    return report("Telegram", PASS, f"token valid, bot is @{me.username}")


def check_gemini(cfg) -> bool:
    if not cfg.gemini_api_key:
        return report("Gemini", SKIP, "no GEMINI_API_KEY set (receipt checks disabled)")
    from google import genai

    try:
        client = genai.Client(api_key=cfg.gemini_api_key)
        client.models.generate_content(model=cfg.gemini_model, contents="ping")
    except Exception as exc:
        return report("Gemini", FAIL, f"API key or model rejected ({describe(exc)})")
    return report("Gemini", PASS, f"key valid, model {cfg.gemini_model} responds")


def check_sheet(cfg) -> bool:
    if not (cfg.google_credentials and cfg.sheet_id):
        return report("Sheet", SKIP, "mirror not configured (optional)")
    from clubbot.sheets import SheetMirror

    try:
        mirror = SheetMirror.from_config(cfg.google_credentials, cfg.sheet_id)
        # Push an empty snapshot: proves API enabled + sheet shared + writable,
        # and creates the Members/Payments tabs so success is visible.
        mirror.push(*mirror.snapshot(db.connect(":memory:")))
    except Exception as exc:
        return report("Sheet", FAIL, f"cannot write to the Sheet ({describe(exc)})")
    return report("Sheet", PASS, "wrote Members/Payments tabs - open the Sheet to see them")


def main() -> int:
    try:
        cfg = config.load_config()
    except SystemExit as exc:
        print(f"[{FAIL}] Config: {exc}")
        return 1
    ok = all([check_telegram(cfg), check_gemini(cfg), check_sheet(cfg)])
    print("\nAll good - start the bot." if ok else "\nFix the FAIL lines above, then rerun.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
