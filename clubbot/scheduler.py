"""Restart-safe lifecycle jobs: term-start blast, deadline reminders, group
progress posts, weekly DB backup, Sheet sync.

One hourly due-check sends whatever is due today and not yet stamped, so a
restart or a crashed run self-heals on the next check without re-sending.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
import tempfile
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden

from clubbot import db
from clubbot.payments import SINGAPORE_TIME

log = logging.getLogger(__name__)

REMINDER_HOUR = 10    # 10:00 SGT term-start blast and DM reminders
GROUP_POST_HOUR = 12  # 12:00 SGT Mon/Thu group progress posts
QUIET_HOUR = 22       # a catch-up after a late restart waits for the next morning
REMINDER_DAYS = (3, 7, 10, 13)  # days after term start; only those before the deadline
GROUP_POST_WEEKDAYS = (0, 3)    # Monday, Thursday
BACKUP_HOUR = 3     # 03:00 SGT daily check; a backup goes out when one is due
BACKUP_EVERY = timedelta(days=7)
SHEET_HOUR = 2      # 02:30 SGT nightly full Sheet rebuild
SHEET_SYNC_DELAY = timedelta(seconds=30)  # debounce for on-change syncs
EXTRACT_RETRY_INTERVAL = timedelta(minutes=15)  # Gemini-failure receipt retries


# --- Pure due-date calculators (no I/O) ----------------------------------------


def reminder_event(term, day: date) -> str | None:
    """The DM reminder due on `day` ('remind-d7' / 'lastcall'), if any."""
    start = date.fromisoformat(term["start_date"])
    deadline = date.fromisoformat(term["deadline"])
    if day == deadline:
        return "lastcall"
    offset = (day - start).days
    if day < deadline and offset in REMINDER_DAYS:
        return f"remind-d{offset}"
    return None


def group_post_event(term, day: date) -> str | None:
    """'grouppost-<date>' on Mondays/Thursdays from term start to the deadline."""
    start = date.fromisoformat(term["start_date"])
    deadline = date.fromisoformat(term["deadline"])
    if start <= day <= deadline and day.weekday() in GROUP_POST_WEEKDAYS:
        return f"grouppost-{day.isoformat()}"
    return None


# --- Async actions (testable with a mock bot + in-memory conn) -----------------


def reminder_keyboard(term_id: int) -> InlineKeyboardMarkup:
    """The amount depends on the member's shirt choice, so blasts and reminders
    carry a button into the /pay flow instead of a ready-made QR."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Pay now", callback_data="pay:start")],
            [
                InlineKeyboardButton(
                    "Not continuing this term", callback_data=f"optout:{term_id}"
                )
            ],
        ]
    )


async def do_term_start_blast(bot, conn: sqlite3.Connection, term_id: int) -> None:
    """Invite every active, not-yet-verified member to pay, then stamp."""
    term = db.get_term(conn, term_id)
    text = (
        f"{term['name']} membership fee collection is open "
        f"(deadline {term['deadline']}).\n"
        "Tap Pay now to choose a shirt option and get your personal PayNow QR."
    )
    retry_later = False
    for member in db.list_active_members(conn):
        payment = db.get_or_create_payment(
            conn, member_id=member["telegram_user_id"], term_id=term_id
        )
        # Skip anyone already paid, already holding a QR from /pay, opted out,
        # or messaged before a mid-blast restart. qr_issued_at is NOT stamped
        # here (it bounds the payment-time check to the real QR).
        if (
            payment["status"] == "verified"
            or payment["qr_issued_at"]
            or payment["opted_out_at"]
            or payment["notified_at"]
        ):
            continue
        if await _deliver(bot, member["telegram_user_id"], text, term_id):
            db.mark_payment_notified(conn, payment["id"])
        else:
            retry_later = True
    if retry_later:
        # Leave the term unstamped: the next hourly check retries only the
        # members still without a notified_at stamp.
        log.warning("Term-start blast for term %s incomplete; will retry", term_id)
        return
    db.mark_term_start_notified(conn, term_id)


async def _deliver(bot, chat_id: int, text: str, term_id: int | None = None) -> bool:
    """Send one message. True = done (sent, or the chat blocked us for good);
    False = a transient failure worth retrying."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reminder_keyboard(term_id) if term_id is not None else None,
        )
    except Forbidden:
        log.info("Chat %s blocked or removed the bot; not retrying", chat_id)
        return True
    except Exception:
        log.warning("Message to chat %s failed; will retry", chat_id, exc_info=True)
        return False
    return True


async def send_unpaid_reminders(
    bot,
    conn: sqlite3.Connection,
    term_id: int,
    *,
    last_call: bool = False,
    event: str | None = None,
) -> int:
    """DM every unpaid, not-opted-out member a nudge; return the successful-send count.

    With `event`, each member is stamped '<event>:<id>' once delivered and
    skipped if already stamped, so a re-run only retries the failures.
    """
    term = db.get_term(conn, term_id)
    if last_call:
        text = (
            f"Last call: today ({term['deadline']}) is the deadline for your "
            f"{term['name']} membership fee. Tap Pay now to get your QR, "
            "then send the payment screenshot here."
        )
    else:
        text = (
            f"Reminder: your {term['name']} membership fee is still unpaid "
            f"(deadline {term['deadline']}). Tap Pay now to get your QR, "
            "then send the payment screenshot here."
        )
    sent = 0
    for member in db.list_unpaid_members(conn, term_id):
        stamp = f"{event}:{member['telegram_user_id']}" if event else None
        if stamp and db.term_event_claimed(conn, term_id, stamp):
            continue
        if await _deliver(bot, member["telegram_user_id"], text, term_id):
            sent += 1
            if stamp:
                db.claim_term_event(conn, term_id, stamp)
    return sent


def group_post_texts(bot, conn: sqlite3.Connection, term) -> dict[str, str]:
    """{settings key: message} for the progress posts. Counts only, never names."""
    tail = (
        f"\nDeadline: {term['deadline']}\n"
        f"Tap to pay: https://t.me/{bot.username}?start=pay"
    )
    return {
        db.GROUP_KEYS["comp"]: (
            f"{term['name']} membership - Competitive: "
            f"{db.roster_paid_count(conn, term['id'])}/{db.roster_size(conn)} paid."
            + tail
        ),
        db.GROUP_KEYS["rec"]: (
            f"{term['name']} membership - "
            f"{db.recreational_paid_count(conn, term['id'])} rec members paid so far."
            + tail
        ),
    }


async def post_group_progress(
    bot, conn: sqlite3.Connection, term, event: str | None = None
) -> None:
    """Post progress to each configured group chat.

    With `event`, each group is stamped '<event>:<key>' once delivered, so a
    failed post is retried on the next hourly check (same day).
    """
    for key, text in group_post_texts(bot, conn, term).items():
        chat_id = db.get_setting(conn, key)
        if chat_id is None:
            continue
        stamp = f"{event}:{key}" if event else None
        if stamp and db.term_event_claimed(conn, term["id"], stamp):
            continue
        if await _deliver(bot, int(chat_id), text) and stamp:
            db.claim_term_event(conn, term["id"], stamp)


async def run_due_events(bot, conn: sqlite3.Connection, now: datetime) -> None:
    """Send everything due today and not yet stamped, for every open term.

    Reminders and group posts are stamped per recipient after delivery, so
    failures retry hourly until the quiet hour. A crash between a send and its
    stamp can repeat that one message (rare; better than losing it).
    """
    now = now.astimezone(SINGAPORE_TIME)
    today = now.date()
    if not REMINDER_HOUR <= now.hour < QUIET_HOUR:
        return
    group_posts_on = db.get_setting(conn, "group_posts") != "off"
    for term in db.list_terms(conn):
        if not term["start_date"] <= today.isoformat() <= term["deadline"]:
            continue
        blasted = False
        if term["start_notified_at"] is None:
            await do_term_start_blast(bot, conn, term["id"])
            blasted = True
        event = reminder_event(term, today)
        if event and blasted:
            # A same-day blast already invited everyone: retire today's
            # reminder so nobody gets two messages in one day.
            db.claim_term_event(conn, term["id"], event)
        elif event and not db.term_event_claimed(conn, term["id"], event):
            await send_unpaid_reminders(
                bot, conn, term["id"], last_call=event == "lastcall", event=event
            )
        event = group_post_event(term, today)
        if event and group_posts_on and now.hour >= GROUP_POST_HOUR:
            await post_group_progress(bot, conn, term, event)


async def do_weekly_backup(bot, conn: sqlite3.Connection) -> bool:
    """DM the treasurer a consistent copy of the database. Never raises."""
    path = None
    try:
        treasurer_id = db.get_treasurer_id(conn)
        if treasurer_id is None:
            log.error("Cannot send database backup: no treasurer is configured")
            return False
        fd, path = tempfile.mkstemp(prefix="clubbot-backup-", suffix=".db")
        os.close(fd)
        # backup() copies a consistent snapshot even while the bot is writing.
        target = sqlite3.connect(path)
        try:
            conn.backup(target)
        finally:
            target.close()
        stamp = datetime.now(SINGAPORE_TIME).date().isoformat()
        with open(path, "rb") as file:
            await bot.send_document(
                chat_id=treasurer_id,
                document=file,
                filename=f"clubbot-{stamp}.db",
                caption=(
                    "Weekly database backup. Keep this file: it holds every "
                    "member, payment and the receipt-reuse history."
                ),
            )
        db.set_setting(conn, "last_backup_at", datetime.now(SINGAPORE_TIME).isoformat())
        return True
    except Exception:
        log.exception("Weekly database backup failed")
        return False
    finally:
        if path is not None:
            with contextlib.suppress(OSError):
                os.remove(path)


# --- JobQueue glue -------------------------------------------------------------


# The hourly check and a startup or /newterm one-shot can overlap; the blast
# stamps each member only after its send, so two runs would double-message.
_DUE_LOCK = asyncio.Lock()


async def _job_due(context) -> None:
    async with _DUE_LOCK:
        await run_due_events(
            context.bot, context.bot_data["db"], datetime.now(SINGAPORE_TIME)
        )


async def _job_sheet_sync(context) -> None:
    mirror = context.bot_data.get("sheet")
    if mirror is None:
        return
    conn = context.bot_data["db"]
    tabs = mirror.snapshot(conn)
    try:
        await asyncio.to_thread(mirror.push, *tabs)
    except Exception:
        log.warning("Google Sheet sync failed", exc_info=True)


def request_sheet_sync(app) -> None:
    """Debounced 'sync the Sheet soon' after a data change.

    No-op when no mirror is configured or the JobQueue is missing; the nightly
    rebuild remains the backstop.
    """
    if app.bot_data.get("sheet") is None or app.job_queue is None:
        return
    for existing in app.job_queue.get_jobs_by_name("sheet-sync"):
        existing.schedule_removal()
    app.job_queue.run_once(_job_sheet_sync, when=SHEET_SYNC_DELAY, name="sheet-sync")


def backup_due(conn: sqlite3.Connection, now: datetime) -> bool:
    """A week since the last successful backup (or never): send one."""
    last = db.get_setting(conn, "last_backup_at")
    return last is None or now - datetime.fromisoformat(last) >= BACKUP_EVERY


async def _job_backup(context) -> None:
    # Checked daily and once at startup, so downtime delays the weekly backup
    # instead of skipping it.
    conn = context.bot_data["db"]
    if backup_due(conn, datetime.now(SINGAPORE_TIME)):
        await do_weekly_backup(context.bot, conn)


async def _job_retry_extractions(context) -> None:
    # Imported here: bot imports this module, and the shared verification
    # path lives in bot.
    from clubbot import bot

    await bot.retry_failed_extractions(context)


def schedule_all(app, conn: sqlite3.Connection) -> None:
    """Arm the recurring jobs. Called once at startup."""
    jq = app.job_queue
    now = datetime.now(SINGAPORE_TIME)
    # Nothing is mid-extraction at startup: receipts a restart cut off become
    # retryable instead of stuck pending (with their image hash reserved).
    db.requeue_interrupted_extractions(conn)
    # Hourly due-check just after each full hour, plus one right away to catch
    # up on anything due while the bot was down. It also retries a blast that
    # failed outright.
    jq.run_repeating(
        _job_due,
        interval=timedelta(hours=1),
        first=now.replace(minute=0, second=30, microsecond=0) + timedelta(hours=1),
        name="due-check",
    )
    jq.run_once(_job_due, when=timedelta(seconds=5), name="due-check-now")
    jq.run_daily(
        _job_backup,
        time=time(hour=BACKUP_HOUR, minute=0, tzinfo=SINGAPORE_TIME),
        name="weekly-backup",
    )
    jq.run_once(_job_backup, when=timedelta(minutes=1), name="backup-catch-up")
    # Nightly Sheet rebuild; the job itself no-ops when no mirror is configured.
    jq.run_daily(
        _job_sheet_sync,
        time=time(hour=SHEET_HOUR, minute=30, tzinfo=SINGAPORE_TIME),
        name="sheet-nightly",
    )
    jq.run_repeating(
        _job_retry_extractions, interval=EXTRACT_RETRY_INTERVAL, name="extract-retry"
    )


def schedule_term_jobs(app, conn: sqlite3.Connection, term_id: int) -> None:
    """Run a due-check now, so /newterm on an already-started term blasts at once."""
    if app.job_queue is None:
        return
    app.job_queue.run_once(_job_due, when=timedelta(seconds=5), name="due-check-now")
