"""/newterm: the one-line form, or (sent on its own) a tap-through wizard.

The wizard keeps its answers in user_data and creates nothing until the
final [Create term] tap, so a restart or /cancel mid-way loses nothing.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import warnings
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from clubbot import db, scheduler
from clubbot.format import money

NAME, START, END, DEADLINE, FEES, CONFIRM = range(6)

NOT_TREASURER = "Only the treasurer can create a term."
CANCELLED = "New term cancelled. Nothing was created."
EXPIRED = "This button has expired. Send /newterm to start again."
OLD_STEP = "That button is from an earlier step. Use the latest message."


def _parse_fee_cents(value: str, *, allow_zero: bool = False) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("fee must be a number") from exc
    # NaN/Infinity pass Decimal() but cannot be compared safely.
    if not amount.is_finite():
        raise ValueError("fee must be finite")
    too_small = amount < 0 if allow_zero else amount <= 0
    if too_small or amount.as_tuple().exponent < -2:
        raise ValueError("fee must be positive with at most 2 decimal places")
    return int(amount * 100)


NEWTERM_USAGE = (
    "Usage: send /newterm on its own and tap through the steps.\n\n"
    "Or in one line: /newterm <name> <start YYYY-MM-DD> <end YYYY-MM-DD> "
    "deadline=YYYY-MM-DD comp=<fee> rec=<fee> recshirt=<fee> shirt=<fee>\n"
    "Example: /newterm Term 1 2026-09-01 2026-12-01 deadline=2026-09-15 "
    "comp=20 rec=25 recshirt=30 shirt=15\n"
    "(comp = competitive fee, shirt = shirt add-on for competitive, "
    "recshirt = recreational fee including the shirt. No shirt this term? "
    "Use shirt=0 and recshirt equal to rec.)"
)
NEWTERM_KEYS = ("deadline", "comp", "rec", "recshirt", "shirt")


def parse_newterm_args(args: list[str]) -> dict:
    """Split /newterm args into create_term kwargs; ValueError on bad input."""
    options = dict(arg.split("=", 1) for arg in args if "=" in arg)
    positional = [arg for arg in args if "=" not in arg]
    unknown = sorted(set(options) - set(NEWTERM_KEYS))
    if unknown:
        raise ValueError(f"unknown option(s): {', '.join(unknown)}")
    missing = [key for key in NEWTERM_KEYS if key not in options]
    if missing:
        raise ValueError(f"missing option(s): {', '.join(missing)}")
    if len(positional) < 3:
        raise ValueError("need a name, a start date and an end date")
    return {
        "name": " ".join(positional[:-2]),
        "start_date": positional[-2],
        "end_date": positional[-1],
        "deadline": options["deadline"],
        "fee_cents": _parse_fee_cents(options["comp"]),
        "rec_fee_cents": _parse_fee_cents(options["rec"]),
        "recshirt_fee_cents": _parse_fee_cents(options["recshirt"]),
        # shirt=0: no shirt for sale to competitive members this term.
        "shirt_fee_cents": _parse_fee_cents(options["shirt"], allow_zero=True),
    }


def term_created_text(term: sqlite3.Row) -> str:
    return (
        f"Term created: {term['name']}\n"
        f"Dates: {term['start_date']} to {term['end_date']}\n"
        f"Deadline: {term['deadline']}\n"
        f"Competitive: {money(db.term_fee(term, 'competitive'))}"
        f" ({money(db.term_fee(term, 'competitive', with_shirt=True))} with shirt)\n"
        f"Recreational: {money(db.term_fee(term, 'recreational'))}"
        f" ({money(db.term_fee(term, 'recreational', with_shirt=True))} with shirt)\n\n"
        "Members can now use /pay while the term is active."
    )


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


async def cmd_newterm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    conn = _db(context)
    if db.get_role(conn, update.effective_user.id) != "treasurer":
        await update.message.reply_text(NOT_TREASURER)
        return ConversationHandler.END
    if not context.args:
        return await _start_wizard(update, context)
    try:
        term = db.create_term(
            conn, **parse_newterm_args(context.args), created_by=update.effective_user.id
        )
    except ValueError as exc:
        await update.message.reply_text(f"Could not create term: {exc}\n\n{NEWTERM_USAGE}")
        return ConversationHandler.END
    scheduler.schedule_term_jobs(context.application, conn, term["id"])
    await update.message.reply_text(term_created_text(term))
    return ConversationHandler.END


# --- wizard -------------------------------------------------------------------

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y")
# (create_term kwarg, question, allow_zero)
FEE_STEPS = (
    ("fee_cents", "Competitive fee (without shirt)?", False),
    ("rec_fee_cents", "Recreational fee (without shirt)?", False),
    (
        "recshirt_fee_cents",
        "Recreational fee WITH shirt?\n(No shirts this term: type the same as the rec fee.)",
        False,
    ),
    (
        "shirt_fee_cents",
        "Shirt add-on price for competitive players?\n(No shirts this term: type 0.)",
        True,
    ),
)
FEE_LABELS = ("comp", "rec", "rec+shirt", "shirt")


def parse_date(text: str) -> date:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(text)


def nice(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b %Y}"


def next_name(name: str) -> str | None:
    """'Term 1' -> 'Term 2'; None when the name has no trailing number."""
    match = re.search(r"(\d+)\s*$", name)
    return f"{name[: match.start(1)]}{int(match.group(1)) + 1}" if match else None


def last_term_fees(term: sqlite3.Row) -> tuple[int, int, int, int]:
    """(comp, rec, rec+shirt, shirt add-on) in cents, as create_term takes them."""
    comp = db.term_fee(term, "competitive")
    return (
        comp,
        db.term_fee(term, "recreational"),
        db.term_fee(term, "recreational", with_shirt=True),
        db.term_fee(term, "competitive", with_shirt=True) - comp,
    )


async def _say(update: Update, context, text: str) -> None:
    """A message with no buttons (errors, results); the current step stays valid."""
    await context.bot.send_message(chat_id=update.effective_user.id, text=text)


async def _send(update: Update, context, text: str, *buttons: tuple[str, str]) -> None:
    """Ask the next question. Each question gets a fresh key, so buttons on any
    earlier message (an older step or an older wizard) are refused."""
    key = _wizard(context)["key"] = secrets.token_hex(3)
    rows = [
        [InlineKeyboardButton(text, callback_data=f"nt:{key}:{value}")]
        for text, value in buttons
    ]
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


async def _answer(update: Update, context) -> str | None:
    """The typed text, or the tapped button's value; None for a stale button."""
    query = update.callback_query
    if query is None:
        return (update.message.text or "").strip()
    _, key, value = query.data.split(":", 2)
    await query.edit_message_reply_markup(reply_markup=None)
    if key != _wizard(context).get("key"):
        await query.answer(OLD_STEP, show_alert=True)
        return None
    await query.answer()
    return value


def _wizard(context) -> dict:
    return context.user_data.setdefault("nt", {})


async def _start_wizard(update: Update, context) -> int:
    terms = db.list_terms(_db(context))
    last = terms[-1] if terms else None
    nt = context.user_data["nt"] = {}
    if last is not None:
        nt["last_fees"] = last_term_fees(last)
        nt["last_days"] = (
            date.fromisoformat(last["end_date"]) - date.fromisoformat(last["start_date"])
        ).days
        nt["suggested_name"] = next_name(last["name"])
    buttons = [(nt["suggested_name"], "suggested")] if nt.get("suggested_name") else []
    await _send(
        update,
        context,
        "New term — step 1 of 5. What is it called? Tap or type a name.\n"
        "(Send /cancel at any point to stop; nothing is created until the last step.)",
        *buttons,
    )
    return NAME


async def on_name(update: Update, context) -> int:
    nt = _wizard(context)
    value = await _answer(update, context)
    if value is None:
        return NAME
    name = nt.get("suggested_name") if value == "suggested" else value
    if not name:
        await _say(update, context, "Please type a name for the term.")
        return NAME
    nt["name"] = name
    today = datetime.now(db.SINGAPORE_TIME).date()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    await _send(
        update,
        context,
        f"{name} — step 2 of 5. When does it start? Tap or type a date "
        "(e.g. 2026-09-01 or 1 Sep 2026).",
        (f"Today ({nice(today)})", f"{today}"),
        (f"Next Monday ({nice(monday)})", f"{monday}"),
    )
    return START


async def on_start(update: Update, context) -> int:
    nt = _wizard(context)
    value = await _answer(update, context)
    if value is None:
        return START
    try:
        start = parse_date(value)
    except ValueError:
        await _say(update, context, "I couldn't read that date. Try e.g. 2026-09-01 or 1 Sep 2026.")
        return START
    nt["start"] = start
    choices = []
    if nt.get("last_days") is not None:
        end = start + timedelta(days=nt["last_days"])
        choices.append((f"Same length as last term ({nice(end)})", f"{end}"))
    end = start + timedelta(weeks=13, days=-1)
    choices.append((f"13 weeks ({nice(end)})", f"{end}"))
    await _send(
        update, context, f"Starts {nice(start)} — step 3 of 5. When does the term end?", *choices
    )
    return END


async def on_end(update: Update, context) -> int:
    nt = _wizard(context)
    value = await _answer(update, context)
    if value is None:
        return END
    try:
        end = parse_date(value)
    except ValueError:
        await _say(update, context, "I couldn't read that date. Try e.g. 2026-12-01 or 1 Dec 2026.")
        return END
    if end < nt["start"]:
        await _say(update, context, f"The end must be on or after the start ({nice(nt['start'])}). Try again.")
        return END
    nt["end"] = end
    choices = [
        (f"{weeks} weeks after start ({nice(due)})", f"{due}")
        for weeks in (2, 3)
        if (due := nt["start"] + timedelta(weeks=weeks)) <= end
    ]
    await _send(
        update,
        context,
        f"Ends {nice(end)} — step 4 of 5. Payment deadline? Reminders stop after it.",
        *choices,
    )
    return DEADLINE


async def on_deadline(update: Update, context) -> int:
    nt = _wizard(context)
    value = await _answer(update, context)
    if value is None:
        return DEADLINE
    try:
        due = parse_date(value)
    except ValueError:
        await _say(update, context, "I couldn't read that date. Try e.g. 2026-09-15 or 15 Sep 2026.")
        return DEADLINE
    if not nt["start"] <= due <= nt["end"]:
        await _say(
            update,
            context,
            f"The deadline must be between {nice(nt['start'])} and {nice(nt['end'])}. Try again.",
        )
        return DEADLINE
    nt["deadline"] = due
    nt["fees"] = []
    return await _ask_fee(update, context)


async def _ask_fee(update: Update, context) -> int:
    nt = _wizard(context)
    step = len(nt["fees"])
    last = nt.get("last_fees")
    choices = []
    if last and step == 0:
        same = " · ".join(f"{label} {money(c)}" for label, c in zip(FEE_LABELS, last))
        choices.append((f"All same as last term: {same}", "all"))
    if last:
        choices.append((f"Same as last term ({money(last[step])})", "same"))
    header = "Step 5 of 5: prices. " if step == 0 else ""
    await _send(
        update,
        context,
        f"{header}{FEE_STEPS[step][1]} Tap or type an amount in dollars (e.g. 20).",
        *choices,
    )
    return FEES


async def on_fee(update: Update, context) -> int:
    nt = _wizard(context)
    value = await _answer(update, context)
    if value is None:
        return FEES
    step = len(nt["fees"])
    last = nt.get("last_fees")
    if value == "all" and last:
        nt["fees"] = list(last)
    elif value == "same" and last:
        nt["fees"].append(last[step])
    else:
        try:
            cents = _parse_fee_cents(value.removeprefix("S$").removeprefix("$"), allow_zero=FEE_STEPS[step][2])
        except ValueError as exc:
            await _say(update, context, f"Not a valid amount ({exc}). Type e.g. 20 or 7.50.")
            return FEES
        nt["fees"].append(cents)
    if len(nt["fees"]) < len(FEE_STEPS):
        return await _ask_fee(update, context)
    comp, rec, recshirt, shirt = nt["fees"]
    shirt_line = (
        f"Shirt: competitive +{money(shirt)}, rec with shirt {money(recshirt)}"
        if shirt or recshirt != rec
        else "No shirts this term"
    )
    await _send(
        update,
        context,
        "Please check:\n\n"
        f"Name: {nt['name']}\n"
        f"Dates: {nice(nt['start'])} to {nice(nt['end'])}\n"
        f"Deadline: {nice(nt['deadline'])}\n"
        f"Competitive: {money(comp)}\nRecreational: {money(rec)}\n{shirt_line}",
        ("✅ Create term", "create"),
        ("Cancel", "cancel"),
    )
    return CONFIRM


async def on_confirm(update: Update, context) -> int:
    value = await _answer(update, context)
    if value is None:
        return CONFIRM
    nt = context.user_data.pop("nt", {})
    if value != "create":
        await _say(update, context, CANCELLED)
        return ConversationHandler.END
    conn = _db(context)
    if db.get_role(conn, update.effective_user.id) != "treasurer":
        await _say(update, context, NOT_TREASURER)
        return ConversationHandler.END
    comp, rec, recshirt, shirt = nt["fees"]
    try:
        term = db.create_term(
            conn,
            name=nt["name"],
            start_date=nt["start"].isoformat(),
            end_date=nt["end"].isoformat(),
            deadline=nt["deadline"].isoformat(),
            fee_cents=comp,
            rec_fee_cents=rec,
            recshirt_fee_cents=recshirt,
            shirt_fee_cents=shirt,
            created_by=update.effective_user.id,
        )
    except ValueError as exc:
        await _say(update, context, f"Could not create term: {exc}\nSend /newterm to start again.")
        return ConversationHandler.END
    scheduler.schedule_term_jobs(context.application, conn, term["id"])
    await _say(update, context, term_created_text(term))
    return ConversationHandler.END


async def on_cancel(update: Update, context) -> int:
    context.user_data.pop("nt", None)
    await update.message.reply_text(CANCELLED)
    return ConversationHandler.END


async def on_expired(update: Update, context) -> None:
    """A wizard button tapped after the wizard ended (or the bot restarted)."""
    await update.callback_query.answer(EXPIRED, show_alert=True)
    await update.callback_query.edit_message_reply_markup(reply_markup=None)


def _guarded(callback):
    async def run(update: Update, context) -> int:
        if "key" not in context.user_data.get("nt", {}):
            if update.callback_query is not None:
                await update.callback_query.answer()
            await _say(update, context, EXPIRED)
            return ConversationHandler.END
        return await callback(update, context)

    return run


def build_handlers(private) -> list:
    text = filters.TEXT & ~filters.COMMAND

    def step(callback):
        callback = _guarded(callback)
        return [CallbackQueryHandler(callback, pattern=r"^nt:"), MessageHandler(text, callback)]

    # PTB warns that buttons aren't tracked per message; intended here (the
    # wizard mixes typed answers and buttons, so it must track per chat).
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*per_message=False")
        wizard = ConversationHandler(
            entry_points=[CommandHandler("newterm", cmd_newterm, filters=private)],
            states={
                NAME: step(on_name),
                START: step(on_start),
                END: step(on_end),
                DEADLINE: step(on_deadline),
                FEES: step(on_fee),
                CONFIRM: [CallbackQueryHandler(_guarded(on_confirm), pattern=r"^nt:")],
            },
            fallbacks=[CommandHandler("cancel", on_cancel)],
            allow_reentry=True,
        )
    return [wizard, CallbackQueryHandler(on_expired, pattern=r"^nt:")]
