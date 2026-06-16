"""Telegram handlers and application wiring (Phase 1: registration + status)."""

import sqlite3

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from clubbot import db, validation

ASK_NAME, ASK_SUTD_ID, CONFIRM = range(3)

WELCOME = (
    "Welcome to the SUTD Badminton Club bot! 🏸\n\n"
    "Let's get you registered.\n"
    "What's your full name, as in SUTD records?"
)
BAD_NAME = "That doesn't look like a name — please send your full name as text (e.g. Alice Tan)."
ASK_ID_TEXT = "Thanks! Now send your 7-digit SUTD student ID (e.g. 1007654)."
BAD_SUTD_ID = "That doesn't look right — your SUTD student ID is exactly 7 digits (e.g. 1007654)."
SUTD_ID_TAKEN = (
    "That SUTD ID is already registered to a different Telegram account.\n"
    "If you switched accounts, ask the treasurer to /relink you. Use /cancel to stop."
)
CONFIRM_PROMPT = (
    "Register as:\n\n  Name: {name}\n  SUTD ID: {sutd_id}\n\n"
    "Reply yes to confirm or no to start over."
)
REGISTERED = (
    "You're registered, {name}! ✅\n"
    "You'll get a message here when membership fee collection opens. Check /status any time."
)
CANCELLED = "Registration cancelled. Send /start whenever you're ready."
NOT_REGISTERED = "You're not registered yet — send /start to register."
HELP_TEXT = (
    "Commands:\n"
    "/start — register (or see your status)\n"
    "/status — your membership status\n"
    "/help — this message\n\n"
    "Paying for membership will be added soon."
)


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


def _status_text(member: sqlite3.Row) -> str:
    handle = f", @{member['username']}" if member["username"] else ""
    return (
        f"You're registered as {member['full_name']} "
        f"(SUTD ID {member['sutd_id']}{handle}).\n\n"
        "Fee collection hasn't started yet — nothing to do for now. "
        "When the treasurer opens the term, I'll DM you a payment QR with instructions."
    )


def _refresh_username(conn: sqlite3.Connection, member: sqlite3.Row, user) -> sqlite3.Row:
    """Usernames change over time; re-sync from Telegram on every contact."""
    if member["username"] != user.username:
        db.update_username(conn, user.id, user.username)
        member = db.get_member(conn, user.id)
    return member


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    member = db.get_member(_db(context), update.effective_user.id)
    if member is not None:
        member = _refresh_username(_db(context), member, update.effective_user)
        await update.message.reply_text(_status_text(member))
        return ConversationHandler.END
    await update.message.reply_text(WELCOME)
    return ASK_NAME


async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = validation.normalize_full_name(update.message.text)
    if name is None:
        await update.message.reply_text(BAD_NAME)
        return ASK_NAME
    context.user_data["full_name"] = name
    await update.message.reply_text(ASK_ID_TEXT)
    return ASK_SUTD_ID


async def on_sutd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sutd_id = validation.normalize_sutd_id(update.message.text)
    if sutd_id is None:
        await update.message.reply_text(BAD_SUTD_ID)
        return ASK_SUTD_ID
    if db.get_member_by_sutd_id(_db(context), sutd_id) is not None:
        await update.message.reply_text(SUTD_ID_TAKEN)
        return ASK_SUTD_ID
    context.user_data["sutd_id"] = sutd_id
    await update.message.reply_text(
        CONFIRM_PROMPT.format(name=context.user_data["full_name"], sutd_id=sutd_id)
    )
    return CONFIRM


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer in ("yes", "y"):
        user = update.effective_user
        db.add_member(
            _db(context),
            telegram_user_id=user.id,
            full_name=context.user_data["full_name"],
            sutd_id=context.user_data["sutd_id"],
            username=user.username,
        )
        name = context.user_data["full_name"]
        context.user_data.clear()
        await update.message.reply_text(REGISTERED.format(name=name))
        return ConversationHandler.END
    if answer in ("no", "n"):
        context.user_data.clear()
        await update.message.reply_text(CANCELLED)
        return ConversationHandler.END
    await update.message.reply_text("Please reply yes or no (or /cancel).")
    return CONFIRM


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(CANCELLED)
    return ConversationHandler.END


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member = db.get_member(_db(context), update.effective_user.id)
    if member is None:
        await update.message.reply_text(NOT_REGISTERED)
    else:
        member = _refresh_username(_db(context), member, update.effective_user)
        await update.message.reply_text(_status_text(member))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


def build_application(token: str, conn: sqlite3.Connection) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["db"] = conn
    registration = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_name)],
            ASK_SUTD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_sutd_id)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(registration)
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    return app
