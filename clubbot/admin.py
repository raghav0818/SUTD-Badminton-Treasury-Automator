"""Telegram handlers for treasurer/admin lifecycle commands."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from datetime import datetime

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
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


# --- the / command menu, per role (cosmetic: every command still checks roles) --

MEMBER_COMMANDS = (
    ("start", "Register or see your status"),
    ("status", "Your membership and payment status"),
    ("pay", "Get your PayNow QR"),
    ("help", "List commands"),
)
ADMIN_COMMANDS = (
    ("unpaid", "Who hasn't paid yet"),
    ("stats", "Term payment summary"),
    ("members", "All registered members"),
    ("roster", "Set the competitive roster"),
    ("setgroup", "Send inside a club group to link it"),
)
TREASURER_COMMANDS = (
    ("newterm", "Open a new term (tap through the steps)"),
    ("markpaid", "Record a cash payment: /markpaid <SUTD ID>"),
    ("remind", "Nudge unpaid members now"),
    ("revoke", "Remove a membership: /revoke <SUTD ID>"),
    ("addadmin", "Make someone an admin: /addadmin <SUTD ID>"),
    ("removeadmin", "Remove an admin (tap to choose)"),
    ("transfertreasurer", "Hand over the treasurer role: /transfertreasurer <SUTD ID>"),
    ("relink", "Member changed Telegram account"),
    ("settings", "PayNow and group post settings"),
)


def menu_for(role: str | None) -> list[BotCommand]:
    commands = MEMBER_COMMANDS
    if role in ("treasurer", "admin"):
        commands += ADMIN_COMMANDS
    if role == "treasurer":
        commands += TREASURER_COMMANDS
    return [BotCommand(name, description) for name, description in commands]


async def sync_command_menu(bot, conn: sqlite3.Connection, user_id: int) -> None:
    """Give this person the / menu for their current role (a private chat's id
    is the user's id)."""
    role = db.get_role(conn, user_id)
    scope = BotCommandScopeChat(user_id)
    try:
        if role is None:
            await bot.delete_my_commands(scope=scope)
        else:
            await bot.set_my_commands(menu_for(role), scope=scope)
    except TelegramError as exc:  # e.g. they never opened the bot
        log.warning("Could not set the command menu for %s: %s", user_id, exc)


async def sync_all_command_menus(bot, conn: sqlite3.Connection) -> None:
    try:
        await bot.set_my_commands(menu_for(None), scope=BotCommandScopeAllPrivateChats())
    except TelegramError as exc:
        log.warning("Could not set the member command menu: %s", exc)
    for row in db.list_admins(conn):
        await sync_command_menu(bot, conn, row["telegram_user_id"])


async def _dm(context, chat_id: int, text: str) -> None:
    """DM a member after an action already saved; a blocked bot must not hide
    the result from the treasurer."""
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except TelegramError as exc:
        log.warning("Could not DM %s: %s", chat_id, exc)


def _confirm_keyboard(data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"cf:{data}"),
                InlineKeyboardButton("Cancel", callback_data="cf:cancel"),
            ]
        ]
    )


def _chunks(text: str, limit: int = 4000) -> list[str]:
    """Split on line breaks so each piece fits one Telegram message (4096 max)."""
    chunks, lines, size = [], [], 0
    for line in text.split("\n"):
        if lines and size + len(line) + 1 > limit:
            chunks.append("\n".join(lines))
            lines, size = [], 0
        lines.append(line)
        size += len(line) + 1
    chunks.append("\n".join(lines))
    return chunks


async def _reply_long(update: Update, text: str, reply_markup=None) -> None:
    pieces = _chunks(text)
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        await update.message.reply_text(piece, reply_markup=reply_markup if last else None)


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
    keyboard = None
    if members and _is_treasurer(conn, update.effective_user.id):
        if len(members) <= MARKPAID_BUTTONS:
            sections.append("Paid you in cash? Tap their name to mark them paid.")
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"Mark paid: {m['full_name']}"[:60],
                            callback_data=f"mp:{term['id']}:{m['telegram_user_id']}",
                        )
                    ]
                    for m in members
                ]
            )
        else:
            sections.append("Paid you in cash? Use /markpaid <SUTD ID>.")
    await _reply_long(update, "\n\n".join(sections), reply_markup=keyboard)


# ponytail: buttons only for short lists; Telegram caps keyboard size (exact cap
# undocumented), and early in a term everyone is unpaid anyway.
MARKPAID_BUTTONS = 30


async def on_markpaid_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """[Mark paid: Alice] under /unpaid: ask to confirm, like /markpaid."""
    query = update.callback_query
    await query.answer()
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await context.bot.send_message(chat_id=update.effective_user.id, text=NOT_TREASURER)
        return
    _, term_id, member_id = query.data.split(":")
    member = db.get_member(conn, int(member_id))
    if member is None:
        return
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        **_markpaid_prompt(conn, member, int(term_id)),
    )


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
    if rejected:
        # A mistyped line would silently drop that player; change nothing.
        await update.message.reply_text(
            "Roster NOT changed. Fix these lines and send the whole list again."
            + rejected_text
        )
        return
    db.replace_roster(conn, rows)
    await update.message.reply_text(f"Roster replaced: {len(rows)} people.")




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
    try:
        me = await context.bot.get_chat_member(update.effective_chat.id, context.bot.id)
        is_admin = me.status in ("administrator", "creator")
    except Exception:
        is_admin = False
    if not is_admin:
        # Telegram only guarantees member lookups (the registration check)
        # for bots that are group admins.
        await update.message.reply_text(
            "Make me an admin of this group first (no special permissions "
            f"needed), then send /setgroup {kind} again."
        )
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
    await _reply_long(update, "Members:\n" + "\n".join(lines))


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
    await update.message.reply_text(**_markpaid_prompt(conn, member, term["id"]))


def _already_paid(conn: sqlite3.Connection, member_id: int, term_id: int) -> bool:
    payment = db.get_payment_for_member_term(conn, member_id=member_id, term_id=term_id)
    return payment is not None and payment["status"] == "verified"


def _markpaid_prompt(conn: sqlite3.Connection, member: sqlite3.Row, term_id: int) -> dict:
    """reply_text/send_message kwargs asking to confirm a manual payment."""
    who = f"{member['full_name']} (SUTD ID {member['sutd_id']})"
    if _already_paid(conn, member["telegram_user_id"], term_id):
        return {"text": f"{who} has already paid this term."}
    return {
        "text": f"Mark {who} as paid for {db.get_term(conn, term_id)['name']}?",
        "reply_markup": _confirm_keyboard(f"markpaid:{term_id}:{member['telegram_user_id']}"),
    }


async def _do_markpaid(context, member_id: int, term_id: int) -> str:
    conn = _db(context)
    if _already_paid(conn, member_id, term_id):
        return "Already marked paid. Nothing changed."
    payment = db.mark_paid_manual(conn, member_id=member_id, term_id=term_id)
    scheduler.request_sheet_sync(context.application)
    await _dm(
        context,
        payment["telegram_user_id"],
        f"Your {money(payment['amount_cents'])} payment for "
        f"{payment['term_name']} has been recorded by the treasurer.",
    )
    return (
        f"Marked {payment['full_name']} as paid for {payment['term_name']} "
        f"({money(payment['amount_cents'])})."
    )


async def _do_revoke(context, member_id: int, term_id: int) -> str:
    try:
        payment = db.revoke_payment(_db(context), member_id=member_id, term_id=term_id)
    except ValueError as exc:
        return f"Could not revoke: {exc}"
    scheduler.request_sheet_sync(context.application)
    await _dm(
        context,
        payment["telegram_user_id"],
        f"Your membership payment for {payment['term_name']} has been revoked "
        "because it could not be confirmed against the bank. "
        "Please contact the treasurer.",
    )
    return f"Revoked {payment['full_name']}'s membership for {payment['term_name']}."


async def _do_transfer(context, old_id: int, member_id: int) -> str:
    conn = _db(context)
    member = db.get_member(conn, member_id)
    try:
        db.transfer_treasurer(conn, new_treasurer_id=member_id)
    except ValueError as exc:
        return f"Could not transfer: {exc}"
    for user_id in (old_id, member_id):
        await sync_command_menu(context.bot, conn, user_id)
    await _dm(
        context,
        member_id,
        "You are now the club treasurer. Send /help to see the treasurer "
        "commands, including payment review.",
    )
    return f"{member['full_name']} is now the treasurer. You remain an admin."


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """[Confirm]/[Cancel] under a /markpaid, /revoke or /transfertreasurer prompt."""
    query = update.callback_query
    await query.answer()
    conn = _db(context)
    action, *ids = query.data.split(":")[1:]
    if action == "cancel":
        await query.edit_message_text("Cancelled. Nothing changed.")
        return
    if not _is_treasurer(conn, update.effective_user.id):
        await query.edit_message_text(NOT_TREASURER)
        return
    # Remove the buttons first so a double tap cannot run the action twice.
    await query.edit_message_reply_markup(reply_markup=None)
    if action == "treasurer":
        result = await _do_transfer(context, update.effective_user.id, int(ids[0]))
    else:
        term_id, member_id = map(int, ids)
        term = db.get_active_term(conn)
        if term is None or term["id"] != term_id:
            result = "That term is no longer the active one. Nothing changed."
        elif action == "markpaid":
            result = await _do_markpaid(context, member_id, term_id)
        else:
            result = await _do_revoke(context, member_id, term_id)
    await query.edit_message_text(result)


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
    who = f"{member['full_name']} (SUTD ID {member['sutd_id']})"
    if not _already_paid(conn, member["telegram_user_id"], term["id"]):
        await update.message.reply_text(
            f"Could not revoke: {who} has no verified payment this term."
        )
        return
    await update.message.reply_text(
        f"Revoke {who}'s membership for {term['name']}? They will be told by DM.",
        reply_markup=_confirm_keyboard(f"revoke:{term['id']}:{member['telegram_user_id']}"),
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
    await sync_command_menu(context.bot, conn, member["telegram_user_id"])
    await update.message.reply_text(f"{member['full_name']} is now an admin.")


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(NOT_TREASURER)
        return
    if not context.args:
        admins = [a for a in db.list_admins(conn) if a["role"] == "admin"]
        if not admins:
            await update.message.reply_text("There are no admins to remove.")
            return
        await update.message.reply_text(
            "Tap the admin to remove:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            a["full_name"] or str(a["telegram_user_id"]),
                            callback_data=f"ra:{a['telegram_user_id']}",
                        )
                    ]
                    for a in admins
                ]
            ),
        )
        return
    member = await _resolve_member(update, context)
    if member is None:
        return
    await update.message.reply_text(await _remove_admin(context, member["telegram_user_id"]))


async def _remove_admin(context, user_id: int) -> str:
    conn = _db(context)
    try:
        db.remove_admin(conn, user_id)
    except ValueError as exc:
        return f"Could not remove admin: {exc}"
    await sync_command_menu(context.bot, conn, user_id)
    member = db.get_member(conn, user_id)
    return f"{member['full_name'] if member else user_id} is no longer an admin."


async def on_removeadmin_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_treasurer(_db(context), update.effective_user.id):
        await query.edit_message_text(NOT_TREASURER)
        return
    await query.edit_message_text(await _remove_admin(context, int(query.data.split(":")[1])))


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
    if member["telegram_user_id"] == update.effective_user.id:
        await update.message.reply_text("Could not transfer: this member is already the treasurer")
        return
    await update.message.reply_text(
        f"Make {member['full_name']} (SUTD ID {member['sutd_id']}) the treasurer? "
        "You will become a normal admin and lose treasurer commands.",
        reply_markup=_confirm_keyboard(f"treasurer:{member['telegram_user_id']}"),
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
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"Cancel {sutd_id}",
                            callback_data=f"rl:x:{sutd_id}:{_armed_stamp(when)}",
                        )
                    ]
                    for sutd_id, when in armed
                ]
            ),
        )
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


def _armed_stamp(armed_at: str) -> int:
    return int(datetime.fromisoformat(armed_at).timestamp())


async def on_relink_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    conn = _db(context)
    if not _is_treasurer(conn, update.effective_user.id):
        await query.edit_message_text(NOT_TREASURER)
        return
    _, _, sutd_id, stamp = query.data.split(":")
    current = dict(db.list_armed_relinks(conn)).get(sutd_id)
    # A newer /relink re-armed it: this old button must not cancel that one.
    if current is None or _armed_stamp(current) != int(stamp):
        await query.edit_message_text(
            f"That relink for SUTD ID {sutd_id} already ended or was re-armed. "
            "Send /relink to see the current list."
        )
        return
    db.disarm_relink(conn, sutd_id)
    await query.edit_message_text(f"Relink cancelled for SUTD ID {sutd_id}.")


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
