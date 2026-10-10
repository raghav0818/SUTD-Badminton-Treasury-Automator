"""One-off: record everyone ALREADY in the rec group chat, so unpaid silent
members can be removed too. (The normal bot interface cannot list a group's
members; from now on the bot sees every join itself.)

Needs Telegram's full API (Telethon), logged in with the bot's own token, and
an api_id/api_hash from https://my.telegram.org -> "API development tools".
Run on the Pi with the bot stopped, after `/setgroup rec`:

    sudo systemctl stop clubbot
    .venv/bin/pip install telethon
    TELEGRAM_API_ID=123 TELEGRAM_API_HASH=abc .venv/bin/python scripts/import_rec_members.py
    sudo systemctl start clubbot

Safe to run again: people already known keep their first-seen date.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient  # noqa: E402  (one-off; not in requirements.txt)
from telethon.sessions import StringSession  # noqa: E402

from clubbot import config, db  # noqa: E402


async def main() -> None:
    cfg = config.load_config()
    conn = db.connect(cfg.db_path)
    chat_id = db.get_setting(conn, db.GROUP_KEYS["rec"])
    if chat_id is None:
        raise SystemExit("Send /setgroup rec inside the rec chat first.")
    api_id, api_hash = os.environ.get("TELEGRAM_API_ID"), os.environ.get("TELEGRAM_API_HASH")
    if not (api_id and api_id.isdigit() and api_hash):
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH (from my.telegram.org).")
    # StringSession: nothing is written to disk.
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start(bot_token=cfg.bot_token)
    async with client:
        imported = 0
        async for user in client.iter_participants(int(chat_id)):
            if user.bot:
                continue
            name = " ".join(filter(None, (user.first_name, user.last_name))) or str(user.id)
            db.note_rec_person(conn, user.id, name)
            imported += 1
        total = (await client.get_participants(int(chat_id), limit=0)).total
    print(f"Imported {imported} people (bots skipped). The chat has {total} members.")
    if imported < total - 5:
        print(
            "WARNING: far fewer than the member count (Telegram may not give a bot "
            "the full list). Fallback: make the next term's rec chat the last new "
            "group, with the bot added before anyone joins."
        )


if __name__ == "__main__":
    asyncio.run(main())
