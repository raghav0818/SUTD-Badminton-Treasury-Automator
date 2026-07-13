"""Telegram handlers for treasurer/admin lifecycle and auditing commands."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace

from telegram import Update
from telegram.ext import ContextTypes

from clubbot import db, scheduler
from clubbot.format import money
from clubbot.payments import SchoolConfig, build_member_qr, school_config

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
    await update.message.reply_text(
        f"Unpaid members for {term['name']}:\n" + "\n".join(lines)
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
    await update.message.reply_text("Members:\n" + "\n".join(lines))


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
    scheduler.request_sheet_sync(context.application)
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
    scheduler.request_sheet_sync(context.application)
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


# --- Phase 4: admin management, relink, settings --------------------------------


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    try:
        db.add_admin(
            conn,
            telegram_user_id=member["telegram_user_id"],
            added_by=update.effective_user.id,
        )
    except ValueError as exc:
        await update.message.reply_text(f"Could not add admin: {exc}")
        return
    await update.message.reply_text(f"{member['full_name']} is now an admin.")


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    try:
        db.remove_admin(conn, member["telegram_user_id"])
    except ValueError as exc:
        await update.message.reply_text(f"Could not remove admin: {exc}")
        return
    await update.message.reply_text(f"{member['full_name']} is no longer an admin.")


async def cmd_transfertreasurer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    try:
        db.transfer_treasurer(conn, new_treasurer_id=member["telegram_user_id"])
    except ValueError as exc:
        await update.message.reply_text(f"Could not transfer: {exc}")
        return
    await update.message.reply_text(
        f"{member['full_name']} is now the treasurer. You remain an admin."
    )
    await context.bot.send_message(
        chat_id=member["telegram_user_id"],
        text=(
            "You are now the club treasurer. Send /help to see the treasurer "
            "commands, including payment review and the weekly audit."
        ),
    )


async def cmd_relink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    if not context.args:
        armed = db.list_armed_relinks(conn)
        if not armed:
            await update.message.reply_text(
                "No relinks are armed.\n"
                "Usage: /relink <sutd_id> to arm, /relink <sutd_id> cancel to disarm."
            )
            return
        lines = ["Armed relinks (each expires 48h after arming):"]
        lines += [f"- SUTD ID {sutd_id}, armed {when}" for sutd_id, when in armed]
        await update.message.reply_text("\n".join(lines))
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    if len(context.args) > 1 and context.args[1].lower() == "cancel":
        db.disarm_relink(conn, member["sutd_id"])
        await update.message.reply_text(
            f"Relink cancelled for {member['full_name']} (SUTD ID {member['sutd_id']})."
        )
        return
    db.arm_relink(conn, member["sutd_id"])
    await update.message.reply_text(
        f"Relink armed for {member['full_name']} (SUTD ID {member['sutd_id']}).\n"
        "Ask them to send /start from their NEW Telegram account and register "
        "with the same SUTD ID within 48 hours. Their payment history will "
        "move over. Cancel with /relink "
        f"{member['sutd_id']} cancel."
    )


SETTING_KEYS = (
    "school_uen",
    "school_merchant_name",
    "school_bill_number",
    "school_recipient_match",
)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    if not context.args:
        current = school_config(conn)
        defaults = SchoolConfig()
        lines = ["Current settings:"]
        for key in SETTING_KEYS:
            attr = key.removeprefix("school_")
            value = getattr(current, attr)
            suffix = " (default)" if value == getattr(defaults, attr) else ""
            lines.append(f"{key} = {value}{suffix}")
        lines.append("\nChange with: /settings <key> <value>")
        await update.message.reply_text("\n".join(lines))
        return
    key = context.args[0]
    if key not in SETTING_KEYS:
        await update.message.reply_text(
            "Unknown setting. Available keys:\n" + "\n".join(SETTING_KEYS)
        )
        return
    if len(context.args) < 2:
        await update.message.reply_text(f"Usage: /settings {key} <value>")
        return
    value = " ".join(context.args[1:]).strip()
    # Dry-run a QR with the candidate value; a bad UEN/merchant name/bill
    # number (non-ASCII, too long) would otherwise break /pay for everyone.
    candidate = replace(school_config(conn), **{key.removeprefix("school_"): value})
    try:
        build_member_qr(fee_cents=100, reference="BDM-0-TEST", school=candidate)
    except Exception as exc:
        await update.message.reply_text(
            f"Not saved - this value cannot be encoded in a PayNow QR: {exc}"
        )
        return
    db.set_setting(conn, key, value)
    await update.message.reply_text(
        f"{key} set to: {value}\n"
        "This affects newly generated QRs and receipt verification immediately."
    )


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
