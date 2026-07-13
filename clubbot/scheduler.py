"""Restart-safe lifecycle jobs: term-start blast, day-7 reminder, weekly audit."""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from clubbot import db
from clubbot.format import money
from clubbot.payments import SINGAPORE_TIME, build_member_qr, school_config

log = logging.getLogger(__name__)

REMINDER_HOUR = 10  # 10:00 SGT for term-start blast and day-7 reminder
AUDIT_HOUR = 9      # 09:00 SGT for the weekly digest
SHEET_HOUR = 2      # 02:30 SGT nightly full Sheet rebuild
SHEET_SYNC_DELAY = timedelta(seconds=30)  # debounce for on-change syncs


# --- Pure run-time calculators (no I/O) ----------------------------------------


def _at_reminder_hour(day: date) -> datetime:
    """The given Singapore date at REMINDER_HOUR:00 SGT."""
    return datetime(
        day.year, day.month, day.day, REMINDER_HOUR, 0, tzinfo=SINGAPORE_TIME
    )


def term_start_run_time(term: sqlite3.Row, now: datetime) -> datetime:
    """The term's start date at 10:00 SGT, or `now` if that moment already passed."""
    when = _at_reminder_hour(date.fromisoformat(term["start_date"]))
    return when if when > now else now


def reminder7_run_time(term: sqlite3.Row) -> datetime:
    """Seven days after the term's start date, at 10:00 SGT."""
    start = date.fromisoformat(term["start_date"])
    return _at_reminder_hour(start + timedelta(days=7))


def pending_term_jobs(
    conn: sqlite3.Connection, now: datetime
) -> list[tuple[str, sqlite3.Row, datetime]]:
    """Outstanding ('start'/'reminder7') jobs for terms that have not yet ended."""
    today = now.astimezone(SINGAPORE_TIME).date()
    jobs: list[tuple[str, sqlite3.Row, datetime]] = []
    for term in db.list_terms(conn):
        if date.fromisoformat(term["end_date"]) < today:
            continue
        if term["start_notified_at"] is None:
            jobs.append(("start", term, term_start_run_time(term, now)))
        if term["reminder7_sent_at"] is None:
            jobs.append(("reminder7", term, reminder7_run_time(term)))
    return jobs


# --- Async actions (testable with a mock bot + in-memory conn) -----------------


async def do_term_start_blast(bot, conn: sqlite3.Connection, term_id: int) -> None:
    """Send the membership QR to every active, not-yet-verified member, then stamp."""
    term = db.get_term(conn, term_id)
    caption = (
        f"{term['name']} membership fee: {money(term['fee_cents'])}\n"
        "Pay using this QR, then send the successful-payment screenshot here. "
        "Expand the transfer details so the amount, recipient, Billing ID, "
        "payment time, and bank reference number are visible."
    )
    school = school_config(conn)
    for member in db.list_active_members(conn):
        payment = db.get_or_create_payment(
            conn, member_id=member["telegram_user_id"], term_id=term_id
        )
        if payment["status"] == "verified":
            continue
        payment = db.mark_qr_issued(conn, payment["id"])
        qr = build_member_qr(
            fee_cents=term["fee_cents"], reference=payment["ref_code"], school=school
        )
        image = io.BytesIO(qr)
        image.name = "membership-paynow.png"
        try:
            await bot.send_photo(
                chat_id=member["telegram_user_id"], photo=image, caption=caption
            )
        except Exception:
            log.warning(
                "Term-start QR failed for member %s",
                member["telegram_user_id"],
                exc_info=True,
            )
    db.mark_term_start_notified(conn, term_id)


async def send_unpaid_reminders(
    bot, conn: sqlite3.Connection, term_id: int
) -> int:
    """DM every unpaid active member a gentle nudge; return the successful-send count."""
    term = db.get_term(conn, term_id)
    text = (
        f"Reminder: your {term['name']} membership fee of "
        f"{money(term['fee_cents'])} is still unpaid. Send /pay to get your QR, "
        "then send the payment screenshot here."
    )
    sent = 0
    for member in db.list_unpaid_members(conn, term_id):
        try:
            await bot.send_message(chat_id=member["telegram_user_id"], text=text)
            sent += 1
        except Exception:
            log.warning(
                "Day-7 reminder failed for member %s",
                member["telegram_user_id"],
                exc_info=True,
            )
    return sent


async def do_reminder7(bot, conn: sqlite3.Connection, term_id: int) -> None:
    """Send the single day-7 nudge to the still-unpaid, then stamp the term."""
    await send_unpaid_reminders(bot, conn, term_id)
    db.mark_term_reminder7_sent(conn, term_id)


def _short_date(payment_timestamp: str | None) -> str:
    if not payment_timestamp:
        return "date n/a"
    try:
        return datetime.fromisoformat(payment_timestamp).date().isoformat()
    except ValueError:
        return "date n/a"


async def do_audit_digest(bot, conn: sqlite3.Connection) -> bool:
    """DM the treasurer the verified payments still awaiting a FLYMAX check."""
    rows = db.list_unconfirmed_verified_payments(conn)
    if not rows:
        return False
    treasurer_id = db.get_treasurer_id(conn)
    if treasurer_id is None:
        log.error("Cannot send audit digest: no treasurer is configured")
        return False
    lines = ["Weekly audit — confirm these in DBS FLYMAX:"]
    for row in rows:
        amount = row["amount_cents"] if row["amount_cents"] is not None else row["fee_cents"]
        lines.append(
            f"• {row['full_name']} — {money(amount)} — "
            f"{_short_date(row['payment_timestamp'])}"
        )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✓ All found", callback_data="audit:allfound")]]
    )
    await bot.send_message(
        chat_id=treasurer_id, text="\n".join(lines), reply_markup=keyboard
    )
    return True


# --- JobQueue glue -------------------------------------------------------------


async def _job_term_start(context) -> None:
    conn = context.bot_data["db"]
    await do_term_start_blast(context.bot, conn, context.job.data["term_id"])


async def _job_reminder7(context) -> None:
    conn = context.bot_data["db"]
    await do_reminder7(context.bot, conn, context.job.data["term_id"])


async def _job_sheet_sync(context) -> None:
    mirror = context.bot_data.get("sheet")
    if mirror is None:
        return
    conn = context.bot_data["db"]
    members, payments = mirror.snapshot(conn)
    try:
        await asyncio.to_thread(mirror.push, members, payments)
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


async def _job_audit(context) -> None:
    # run_daily fires every day; act only on Mondays so the digest is weekly
    # without depending on PTB's day indexing.
    if datetime.now(SINGAPORE_TIME).weekday() != 0:
        return
    conn = context.bot_data["db"]
    await do_audit_digest(context.bot, conn)


def _arm_term_job(
    jq, kind: str, term_id: int, when: datetime, now: datetime
) -> None:
    """Schedule one named one-shot term job, replacing any existing job of that name."""
    name = f"{kind}-{term_id}"
    for existing in jq.get_jobs_by_name(name):
        existing.schedule_removal()
    callback = _job_term_start if kind == "start" else _job_reminder7
    jq.run_once(
        callback,
        when=max(when, now + timedelta(seconds=5)),
        data={"term_id": term_id},
        name=name,
    )


def schedule_all(app, conn: sqlite3.Connection) -> None:
    """Restore outstanding one-shot term jobs and arm the daily audit check.

    Called once at startup; restores any blast/reminder a restart would lose.
    """
    jq = app.job_queue
    now = datetime.now(SINGAPORE_TIME)
    for kind, term, when in pending_term_jobs(conn, now):
        _arm_term_job(jq, kind, term["id"], when, now)
    jq.run_daily(
        _job_audit,
        time=time(hour=AUDIT_HOUR, minute=0, tzinfo=SINGAPORE_TIME),
        name="audit-digest",
    )
    # Nightly Sheet rebuild; the job itself no-ops when no mirror is configured.
    jq.run_daily(
        _job_sheet_sync,
        time=time(hour=SHEET_HOUR, minute=30, tzinfo=SINGAPORE_TIME),
        name="sheet-nightly",
    )


def schedule_term_jobs(app, conn: sqlite3.Connection, term_id: int) -> None:
    """Arm a single term's jobs right away.

    Used by /newterm while the bot is already running, since schedule_all only
    runs at startup. Safe to call repeatedly: each job replaces its prior self.
    """
    jq = app.job_queue
    if jq is None:
        return
    now = datetime.now(SINGAPORE_TIME)
    for kind, term, when in pending_term_jobs(conn, now):
        if term["id"] == term_id:
            _arm_term_job(jq, kind, term["id"], when, now)
