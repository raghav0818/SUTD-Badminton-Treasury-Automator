"""/reconcile: the treasurer sends FLYMAX screenshots; every receipt-verified
payment must find its own bank line. A member's receipt can be faked, the
school's bank list can't, so this is the check that money actually arrived.

Matching rules live in payments.reconcile; this module is the Telegram flow.
Nothing is ever revoked automatically: a mismatch gets [Revoke] / [It's fine].
"""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from clubbot import admin, db, scheduler
from clubbot.format import money
from clubbot.payments import SINGAPORE_TIME, BankLine, reconcile, school_config

log = logging.getLogger(__name__)

LINES_KEY = "reconcile_lines"  # user_data: bank lines collected so far
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_FLAG_BUTTONS = 30
WAITING_NUDGE_DAYS = 2

DONE_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("✅ Done, check now", callback_data="rc:done"),
            InlineKeyboardButton("Cancel", callback_data="rc:cancel"),
        ]
    ]
)


def collecting(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True while the treasurer is sending FLYMAX screenshots (not receipts)."""
    return context.user_data.get(LINES_KEY) is not None


async def cmd_reconcile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = admin._db(context)
    if not admin._is_treasurer(conn, update.effective_user.id):
        await update.message.reply_text(admin.NOT_TREASURER)
        return
    if db.get_active_term(conn) is None:
        await update.message.reply_text(admin.NO_TERM)
        return
    if context.bot_data.get("extractor") is None:
        await update.message.reply_text("Screenshot reading is not configured (no Gemini key).")
        return
    context.user_data[LINES_KEY] = []
    await update.message.reply_text(
        "Send me screenshots of the FLYMAX transaction list.\n\n"
        "- Start from today and scroll back to the last time you did this "
        "(or the start of the term).\n"
        "- Don't skip any part of the list. Overlapping screenshots are fine.\n"
        "- Then tap Done.",
        reply_markup=DONE_KEYBOARD,
    )


async def on_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo from the treasurer while collecting: read the bank lines on it."""
    message = update.message
    if message.photo:
        file_id, mime_type, size = message.photo[-1].file_id, "image/jpeg", message.photo[-1].file_size
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id, mime_type, size = (
            message.document.file_id,
            message.document.mime_type,
            message.document.file_size,
        )
    else:
        await message.reply_text("Please send the FLYMAX screenshot as a photo.")
        return
    if size and size > MAX_IMAGE_BYTES:
        await message.reply_text("That image is too large. Please send one under 8 MB.")
        return
    try:
        telegram_file = await context.bot.get_file(file_id)
        image = bytes(await telegram_file.download_as_bytearray())
        lines = await context.bot_data["extractor"].extract_bank_lines(image, mime_type)
    except Exception:
        log.exception("FLYMAX screenshot extraction failed")
        await message.reply_text("I couldn't read that screenshot. Please send it again.")
        return
    collected = context.user_data.setdefault(LINES_KEY, [])
    collected.extend(lines)
    await message.reply_text(
        f"Read {len(lines)} payment(s) from this screenshot ({len(collected)} so far). "
        "Send more, or tap Done.",
        reply_markup=DONE_KEYBOARD,
    )


def _when(line: BankLine) -> str:
    return (line.paid_at or "?").replace("T", " ")[:16]


def _report(conn, result, payments) -> tuple[str, InlineKeyboardMarkup | None]:
    by_id = {p["id"]: p for p in payments}
    counts = db.bank_counts(conn, payments[0]["term_id"]) if payments else None
    sections = [f"✅ Newly confirmed by the bank: {len(result.confirmed)}"]
    if counts:
        sections[0] += f" (total confirmed {counts['confirmed']}/{len(payments)})"
    if result.covered:
        start, end = (t.astimezone(SINGAPORE_TIME).strftime("%d %b %H:%M") for t in result.covered)
        sections.append(f"Screenshots cover {start} to {end}.")
    flagged = [(pid, f"no bank payment found (receipt time {_receipt_time(by_id[pid])})") for pid in result.missing]
    flagged += [
        (pid, f"bank shows {money(line.amount_cents)} from {line.payer_name or '?'} at {_when(line)}: different name")
        for pid, line in result.name_differs
    ]
    flagged += [
        (
            pid,
            "was confirmed, but another member's receipt now claims the same bank "
            "payment; check FLYMAX by hand"
            if pid in result.suspended
            else "one bank payment fits this AND another member's receipt; check FLYMAX by hand",
        )
        for pid in result.ambiguous
    ]
    if flagged:
        sections.append(
            "⚠️ Check these (could be a fake receipt, or e.g. a parent paid):\n"
            + "\n".join(
                f"- {by_id[pid]['full_name']} ({by_id[pid]['sutd_id']}), "
                f"{money(by_id[pid]['amount_cents'])}: {why}"
                for pid, why in flagged
            )
        )
    if result.waiting:
        sections.append(
            f"⏳ {result.waiting} payment(s) are outside these screenshots' dates; "
            "they'll be checked next time."
        )
    if result.stray_lines:
        sections.append(
            "🏦 Bank payments no member's receipt claims (someone who hasn't "
            "sent a receipt yet, or not a club payment):\n"
            + "\n".join(
                f"- {money(line.amount_cents)} from {line.payer_name or '?'} at {_when(line)}"
                for line in result.stray_lines
            )
        )
    if result.no_txn_lines:
        sections.append(
            "🔎 I couldn't read the TXN ID on these rows, so they can't confirm "
            "anyone. Send a clearer screenshot of them with /reconcile:\n"
            + "\n".join(
                f"- {money(line.amount_cents)} from {line.payer_name or '?'} at {_when(line)}"
                for line in result.no_txn_lines
            )
        )
    if result.unreadable_lines:
        sections.append(f"({result.unreadable_lines} row(s) had no readable amount or time and were skipped.)")
    keyboard = None
    if flagged and len(flagged) <= MAX_FLAG_BUTTONS:
        sections.append("Tap Revoke (you'll be asked to confirm) or It's fine.")
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Revoke {by_id[pid]['full_name']}"[:40],
                        callback_data=f"rc:rv:{pid}:{db.payment_version(by_id[pid])}",
                    ),
                    InlineKeyboardButton(
                        "It's fine", callback_data=f"rc:ok:{pid}:{db.payment_version(by_id[pid])}"
                    ),
                ]
                for pid, _ in flagged
            ]
        )
    elif flagged:
        sections.append("Too many to list as buttons: use /revoke <SUTD ID> for fakes.")
    return "\n\n".join(sections), keyboard


def _receipt_time(payment) -> str:
    return (payment["payment_timestamp"] or "?").replace("T", " ")[:16]


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    conn = admin._db(context)
    user_id = update.effective_user.id
    if not admin._is_treasurer(conn, user_id):
        await context.bot.send_message(chat_id=user_id, text=admin.NOT_TREASURER)
        return
    action, *rest = query.data.split(":")[1:]
    if action == "cancel":
        context.user_data.pop(LINES_KEY, None)
        await query.edit_message_text("Cancelled. Nothing changed.")
        return
    if action == "done":
        lines = context.user_data.pop(LINES_KEY, None)
        if lines is None:
            await query.edit_message_text("This check already ran. Send /reconcile to start again.")
            return
        await query.edit_message_reply_markup(reply_markup=None)
        await _run(context, user_id, lines)
        return
    payment = db.get_payment(conn, int(rest[0]))
    if payment is None or payment["status"] != "verified":
        await context.bot.send_message(chat_id=user_id, text="That payment is no longer verified. Nothing to do.")
        return
    if db.payment_version(payment) != int(rest[1]):
        await context.bot.send_message(
            chat_id=user_id,
            text="That button is for an older receipt; the member has sent a new "
            "one since. Nothing changed. Run /reconcile again to check the new one.",
        )
        return
    if action == "ok":
        db.set_bank_confirmed(conn, [payment["id"]])
        scheduler.request_sheet_sync(context.application)
        await context.bot.send_message(
            chat_id=user_id, text=f"OK: {payment['full_name']}'s payment is marked as confirmed."
        )
        return
    # rv: the same confirm step as /revoke (which also DMs the member).
    await context.bot.send_message(
        chat_id=user_id,
        text=f"Revoke {payment['full_name']}'s membership for {payment['term_name']}? "
        "They will be told by DM that the payment couldn't be confirmed against the bank.",
        reply_markup=admin._confirm_keyboard(
            f"revoke:{payment['term_id']}:{payment['member_id']}:{db.payment_version(payment)}"
        ),
    )


async def _run(context, user_id: int, lines: list[BankLine]) -> None:
    conn = admin._db(context)
    term = db.get_active_term(conn)
    if term is None:
        await context.bot.send_message(chat_id=user_id, text=admin.NO_TERM)
        return
    if not lines:
        await context.bot.send_message(
            chat_id=user_id, text="I didn't read any payments. Send /reconcile to start again."
        )
        return
    payments = db.list_reconcile_payments(conn, term["id"])
    result = reconcile(payments, lines, school_config(conn), db.bank_line_owners(conn))
    db.record_reconcile(
        conn,
        suspended=list(result.suspended),
        confirmed=list(result.confirmed),
        claims=list(result.claimed_lines),
        flagged=list(result.missing)
        + list(result.ambiguous)
        + [pid for pid, _ in result.name_differs],
    )
    scheduler.request_sheet_sync(context.application)
    text, keyboard = _report(conn, result, db.list_reconcile_payments(conn, term["id"]))
    pieces = admin._chunks(text)
    for i, piece in enumerate(pieces):
        await context.bot.send_message(
            chat_id=user_id, text=piece, reply_markup=keyboard if i == len(pieces) - 1 else None
        )


def waiting_nudge(conn, now) -> str:
    """Line for the weekly backup DM while receipts still wait for a bank check."""
    term = db.get_active_term(conn)
    if term is None:
        return ""
    cutoff = now.timestamp() - WAITING_NUDGE_DAYS * 86400
    waiting = [
        p
        for p in db.list_reconcile_payments(conn, term["id"])
        if not p["audit_confirmed_at"] and _verified_ts(p) < cutoff
    ]
    if not waiting:
        return ""
    return (
        f"\n\n{len(waiting)} payment(s) still need a bank check. Send /reconcile "
        "with screenshots of the FLYMAX list to confirm them."
    )


def _verified_ts(payment) -> float:
    try:
        return datetime.fromisoformat(payment["verified_at"]).timestamp()
    except (TypeError, ValueError):
        return 0.0


def build_handlers(private_filter) -> list:
    return [
        CommandHandler("reconcile", cmd_reconcile, filters=private_filter),
        CallbackQueryHandler(on_button, pattern=r"^rc:(done|cancel|rv:\d+:\d+|ok:\d+:\d+)$"),
    ]
