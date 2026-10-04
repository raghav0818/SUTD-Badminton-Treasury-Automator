"""Telegram handlers for treasurer/admin lifecycle commands."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace

from telegram import Update
from telegram.ext import ContextTypes

from clubbot import db, scheduler, validation
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
    never_opened = db.list_roster_unregistered(conn)
    opted_out = db.count_opted_out(conn, term["id"])
    sections = [] if members or never_opened else ["Everyone has paid."]
    if members:
        sections.append(
            f"Unpaid members for {term['name']}:\n"
            + "\n".join(f"- {m['full_name']} (SUTD ID {m['sutd_id']})" for m in members)
        )
    if never_opened:
        sections.append(
            "On the roster but never opened the bot:\n"
            + "\n".join(f"- {r['name']} (SUTD ID {r['sutd_id']})" for r in never_opened)
        )
    if opted_out:
        sections.append(f"Opted out: {opted_out}")
    await update.message.reply_text("\n\n".join(sections))


def _breakdown_lines(rows) -> list[str]:
    """`Competitive+shirt S$35.00 × 3 = S$105.00` lines plus a total."""
    lines = []
    total = 0
    for row in rows:
        label = "Competitive" if row["category"] == "competitive" else "Rec"
        if row["with_shirt"]:
            label += "+shirt"
        if row["amount_cents"] is None:
            lines.append(f"{label} (amount unknown) × {row['n']}")
            continue
        subtotal = row["amount_cents"] * row["n"]
        total += subtotal
        lines.append(
            f"{label} {money(row['amount_cents'])} × {row['n']} = {money(subtotal)}"
        )
    lines.append(f"Total: {money(total)}")
    return lines


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
    breakdown = _breakdown_lines(db.get_term_amount_breakdown(conn, term["id"]))
    await update.message.reply_text(
        f"{term['name']}\n"
        + "\n".join(breakdown)
        + f"\n{db.roster_paid_count(conn, term['id'])}/{db.roster_size(conn)} roster paid\n\n"
        f"Registered: {stats['registered']}\n"
        f"Paid: {stats['paid']}\n"
        f"Unpaid: {stats['unpaid']}\n"
        f"Opted out: {stats['opted_out']}\n"
        f"Exceptions: {stats['exceptions']}"
    )


def parse_roster(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """`/roster` message → ([(name, sutd_id)], rejected lines). One `Name, ID` per line."""
    parts = text.split(maxsplit=1)  # drop the /roster command itself
    rows: dict[str, str] = {}
    rejected = []
    for line in (parts[1] if len(parts) > 1 else "").splitlines():
        if not line.strip():
            continue
        name, _, raw_id = line.rpartition(",")
        name = validation.normalize_full_name(name)
        sutd_id = validation.normalize_sutd_id(raw_id)
        if name is None or sutd_id is None or sutd_id in rows:
            rejected.append(line.strip())
            continue
        rows[sutd_id] = name
    return [(name, sutd_id) for sutd_id, name in rows.items()], rejected


async def cmd_roster(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_admin(conn, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN)
        return
    rows, rejected = parse_roster(update.message.text or "")
    if not rows and not rejected:
        await update.message.reply_text(
            f"Competitive roster size: {db.roster_size(conn)}\n"
            "To replace it, send /roster followed by one line per person:\n"
            "Alice Tan, 1010123\nBob Lim, 1010456"
        )
        return
    rejected_text = (
        "\n\nRejected lines (each must be: Name, 1010xxx; no duplicate IDs):\n"
        + "\n".join(f"- {line}" for line in rejected)
        if rejected
        else ""
    )
    if not rows:
        # Never wipe the roster because of a badly formatted paste.
        await update.message.reply_text("Roster NOT changed." + rejected_text)
        return
    db.replace_roster(conn, rows)
    await update.message.reply_text(
        f"Roster replaced: {len(rows)} people." + rejected_text
    )




async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/setgroup rec|comp`, sent inside the club group chat it should name."""
    conn = _db(context)
    if not _is_admin(conn, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN)
        return
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "Send this inside the group chat itself: add me to the group, "
            "then send /setgroup rec or /setgroup comp there."
        )
        return
    kind = context.args[0].lower() if context.args else ""
    if kind not in db.GROUP_KEYS:
        await update.message.reply_text("Usage: /setgroup rec or /setgroup comp")
        return
    db.set_setting(conn, db.GROUP_KEYS[kind], str(update.effective_chat.id))
    await update.message.reply_text(
        f"This chat is now the {kind} group. Progress posts (counts only, no "
        "names) go here on Mondays and Thursdays until the deadline."
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
            f"Your {money(payment['amount_cents'])} payment for "
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
            "commands, including payment review."
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
ALL_SETTING_KEYS = SETTING_KEYS + ("group_posts",)


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
        group_posts = db.get_setting(conn, "group_posts") or "on (default)"
        lines.append(f"group_posts = {group_posts}")
        lines.append("\nChange with: /settings <key> <value>")
        await update.message.reply_text("\n".join(lines))
        return
    key = context.args[0]
    if key not in ALL_SETTING_KEYS:
        await update.message.reply_text(
            "Unknown setting. Available keys:\n"
            + "\n".join(ALL_SETTING_KEYS)
        )
        return
    if len(context.args) < 2:
        await update.message.reply_text(f"Usage: /settings {key} <value>")
        return
    value = " ".join(context.args[1:]).strip()
    if key == "group_posts":
        if value.lower() not in ("on", "off"):
            await update.message.reply_text("Usage: /settings group_posts on|off")
            return
        db.set_setting(conn, key, value.lower())
        await update.message.reply_text(f"Group progress posts turned {value.lower()}.")
        return
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
