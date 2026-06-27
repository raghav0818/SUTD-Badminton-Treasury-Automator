"""Telegram handlers for treasurer/admin lifecycle and auditing commands."""

from __future__ import annotations

import logging
import sqlite3

from telegram import Update
from telegram.ext import ContextTypes

from clubbot import db, ops, scheduler
from clubbot.format import money

log = logging.getLogger(__name__)

NOT_ADMIN = "This command is for club admins."
NOT_TREASURER = "Only the treasurer can use this command."
NO_TERM = "Fee collection is not open."


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


def _is_admin(conn: sqlite3.Connection, uid: int) -> bool:
    return db.get_role(conn, uid) in ("treasurer", "admin")


def _is_treasurer(conn: sqlite3.Connection, uid: int) -> bool:
    return db.get_role(conn, uid) == "treasurer"


async def _resolve_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> sqlite3.Row | None:
    """Read a SUTD ID from context.args; reply a friendly error and return None
    when it is missing or unknown."""
    if not context.args:
        await update.message.reply_text("Usage: include the member's 7-digit SUTD ID.")
        return None
    sutd_id = context.args[0]
    member = db.get_member_by_sutd_id(_db(context), sutd_id)
    if member is None:
        await update.message.reply_text(f"No member is registered with SUTD ID {sutd_id}.")
        return None
    return member


async def cmd_unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_admin(conn, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN)
        return
    term = db.get_active_term(conn)
    if term is None:
        await update.message.reply_text(NO_TERM)
        return
    members = db.list_unpaid_members(conn, term["id"])
    if not members:
        await update.message.reply_text("Everyone has paid.")
        return
    lines = [
        f"- {m['full_name']} (SUTD ID {m['sutd_id']})" for m in members
    ]
    await ops.reply_long(
        update.message, f"Unpaid members for {term['name']}:\n" + "\n".join(lines)
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_admin(conn, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN)
        return
    term = db.get_active_term(conn)
    if term is None:
        await update.message.reply_text(NO_TERM)
        return
    stats = db.get_term_payment_stats(conn, term["id"])
    await update.message.reply_text(
        f"{term['name']} ({money(term['fee_cents'])})\n"
        f"Registered: {stats['registered']}\n"
        f"Paid: {stats['paid']}\n"
        f"Unpaid: {stats['unpaid']}\n"
        f"Exceptions: {stats['exceptions']}\n"
        f"Flagged: {stats['flagged']}"
    )


async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_admin(conn, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN)
        return
    members = db.list_members(conn)
    if not members:
        await update.message.reply_text("No members registered yet.")
        return
    lines = []
    for m in members:
        handle = f" @{m['username']}" if m["username"] else ""
        lines.append(f"- {m['full_name']} (SUTD ID {m['sutd_id']}){handle}")
    await ops.reply_long(update.message, "Members:\n" + "\n".join(lines))


async def cmd_markpaid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    term = db.get_active_term(conn)
    if term is None:
        await update.message.reply_text(NO_TERM)
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    payment = db.mark_paid_manual(
        conn, member_id=member["telegram_user_id"], term_id=term["id"]
    )
    ops.mark_dirty(context)
    await update.message.reply_text(
        f"Marked {payment['full_name']} as paid for {payment['term_name']}."
    )
    await context.bot.send_message(
        chat_id=payment["telegram_user_id"],
        text=(
            f"Your {money(payment['fee_cents'])} payment for "
            f"{payment['term_name']} has been recorded by the treasurer."
        ),
    )


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    term = db.get_active_term(conn)
    if term is None:
        await update.message.reply_text(NO_TERM)
        return
    count = await scheduler.send_unpaid_reminders(context.bot, conn, term["id"])
    await update.message.reply_text(f"Reminder sent to {count} member(s).")


async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    sent = await scheduler.do_audit_digest(context.bot, conn)
    if not sent:
        await update.message.reply_text(
            "No verified payments are waiting to be audited."
        )
        return
    await update.message.reply_text("Audit digest sent.")


async def cmd_flag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    term = db.get_active_term(conn)
    if term is None:
        await update.message.reply_text(NO_TERM)
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    try:
        payment = db.flag_payment(
            conn, member_id=member["telegram_user_id"], term_id=term["id"]
        )
    except ValueError as exc:
        await update.message.reply_text(f"Could not flag: {exc}")
        return
    ops.mark_dirty(context)
    await update.message.reply_text(
        f"Flagged {payment['full_name']}'s payment for {payment['term_name']}."
    )


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    term = db.get_active_term(conn)
    if term is None:
        await update.message.reply_text(NO_TERM)
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    try:
        payment = db.revoke_payment(
            conn, member_id=member["telegram_user_id"], term_id=term["id"]
        )
    except ValueError as exc:
        await update.message.reply_text(f"Could not revoke: {exc}")
        return
    ops.mark_dirty(context)
    await update.message.reply_text(
        f"Revoked {payment['full_name']}'s membership for {payment['term_name']}."
    )
    await context.bot.send_message(
        chat_id=payment["telegram_user_id"],
        text=(
            f"Your membership payment for {payment['term_name']} has been revoked "
            "because it could not be confirmed against the bank. "
            "Please contact the treasurer."
        ),
    )


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force a Google Sheet rebuild (treasurer-only)."""
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    syncer = context.bot_data.get("sheet_syncer")
    if syncer is None:
        await update.message.reply_text("The Google Sheet mirror is not configured.")
        return
    syncer.mark_dirty()
    await update.message.reply_text("Google Sheet refresh queued.")


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Write an on-demand database backup (treasurer-only)."""
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    db_path = context.bot_data.get("db_path")
    if not db_path:
        await update.message.reply_text("No database path is configured for backups.")
        return
    try:
        path = ops.backup_database(db_path)
    except Exception as exc:  # never crash on a backup attempt
        log.exception("Manual backup failed")
        await update.message.reply_text(f"Backup failed: {exc}")
        return
    await update.message.reply_text(f"Backup written to {path}")


async def on_audit_allfound(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await query.edit_message_text(NOT_TREASURER)
        return
    payments = db.list_unconfirmed_verified_payments(conn)
    db.confirm_payments_audited(conn, [p["id"] for p in payments])
    db.record_audit(
        conn,
        period_start=None,
        period_end=None,
        payment_count=len(payments),
        result="all_found",
    )
    await query.edit_message_text(
        f"Marked {len(payments)} payment(s) as found in FLYMAX."
    )
