"""Cross-cutting robustness: error handling, rate limiting, backups, chunking.

This module is part of the core so every other module can import its helpers
without a circular dependency. Nothing here may raise into a user's flow.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable

from telegram.ext import ContextTypes

from clubbot import db

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096


# --- Message chunking ----------------------------------------------------------


def chunk_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split text into <=limit pieces, preferring line boundaries.

    A single line longer than `limit` is hard-split; otherwise lines are packed
    so that each chunk stays whole and under the Telegram message-length cap.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single over-long line
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


async def reply_long(message, text: str) -> None:
    """reply_text, transparently split across the 4096-char limit."""
    for part in chunk_text(text):
        await message.reply_text(part)


# --- Rate limiting -------------------------------------------------------------


class RateLimiter:
    """In-memory per-key limiter: a max count per rolling window + min interval.

    State lives only in memory and resets on restart — acceptable, since its
    job is to blunt floods/abuse and protect the Gemini quota, not to enforce a
    hard accounting limit.
    """

    def __init__(
        self,
        *,
        max_per_window: int,
        window_seconds: float,
        min_interval_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max = max_per_window
        self.window = window_seconds
        self.min_interval = min_interval_seconds
        self._clock = clock
        self._hits: dict[object, list[float]] = {}

    def allow(self, key) -> bool:
        """True if this call is within limits (and records it); False to reject."""
        now = self._clock()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if hits and self.min_interval and now - hits[-1] < self.min_interval:
            self._hits[key] = hits
            return False
        if len(hits) >= self.max:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


# --- Database backups ----------------------------------------------------------


def backup_database(
    db_path: str, *, backups_dir: str = "backups", keep: int = 14
) -> str:
    """Write a consistent copy of the live DB via SQLite's online-backup API.

    Safe to run while the bot is using the database. Keeps only the newest
    `keep` backups. Returns the path written.
    """
    os.makedirs(backups_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = os.path.join(backups_dir, f"clubbot-{stamp}.db")
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(out)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    existing = sorted(
        (
            name
            for name in os.listdir(backups_dir)
            if name.startswith("clubbot-") and name.endswith(".db")
        ),
        reverse=True,
    )
    for stale in existing[keep:]:
        os.remove(os.path.join(backups_dir, stale))
    return out


# --- Global error handler ------------------------------------------------------

_last_alert = {"t": 0.0}
_ALERT_INTERVAL = 300.0  # min seconds between treasurer error DMs


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any unhandled handler/job error and DM the treasurer (rate-limited).

    The bot keeps running; this exists so failures are never silent and a storm
    of errors cannot spam the treasurer.
    """
    log.error("Unhandled error", exc_info=context.error)
    now = time.monotonic()
    if now - _last_alert["t"] < _ALERT_INTERVAL:
        return
    _last_alert["t"] = now
    conn = context.bot_data.get("db")
    if conn is None:
        return
    treasurer_id = db.get_treasurer_id(conn)
    if treasurer_id is None:
        return
    try:
        await context.bot.send_message(
            chat_id=treasurer_id,
            text=(
                f"⚠️ The bot hit an internal error: {type(context.error).__name__}. "
                "It is still running; check the logs if this keeps happening."
            ),
        )
    except Exception:
        log.warning("Could not DM the treasurer about an error", exc_info=True)


def reset_error_alert_throttle() -> None:
    """Test hook: clear the inter-alert cooldown."""
    _last_alert["t"] = 0.0


# --- Sheet sync trigger --------------------------------------------------------


def mark_dirty(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Queue a Google Sheet rebuild if a syncer is configured; otherwise no-op."""
    syncer = context.bot_data.get("sheet_syncer")
    if syncer is not None:
        syncer.mark_dirty()
