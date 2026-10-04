"""Telegram handlers for registration, terms, and payment verification."""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from clubbot import admin, db, scheduler, validation
from clubbot.format import money
from clubbot.payments import (
    build_member_qr,
    normalise_transaction_id,
    school_config,
    verify_extracted_payment,
)

log = logging.getLogger(__name__)

ASK_NAME, ASK_SUTD_ID, CONFIRM = range(3)
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_EXTRACT_ATTEMPTS = 4  # the first try plus three 15-minute retries
# Settings written by /setgroup; registration requires being in one of them.
IN_GROUP_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}

WELCOME = (
    "Welcome to the SUTD Badminton Club bot!\n\n"
    "Privacy: to run club membership payments, this bot stores your Telegram "
    "ID, name, SUTD ID, and the payment details read from your receipt "
    "screenshot. The screenshot itself is never saved.\n\n"
    "Let's get you registered.\n"
    "What's your full name, as in SUTD records?"
)
BAD_NAME = (
    "That doesn't look like a name. Please send your full name as text "
    "(for example, Alice Tan)."
)
ASK_ID_TEXT = "Thanks! Now send your 7-digit SUTD student ID (for example, 1010234)."
BAD_SUTD_ID = "Your SUTD student ID must be exactly 7 digits and start with 1010 (e.g. 1010234). Please try again."
SUTD_ID_TAKEN = (
    "That SUTD ID is already registered to a different Telegram account.\n"
    "If you switched accounts, ask the treasurer to relink you. Use /cancel to stop."
)
CONFIRM_PROMPT = (
    "Register as:\n\nName: {name}\nSUTD ID: {sutd_id}\n\n"
    "Reply yes to confirm or no to start over."
)
REGISTERED = (
    "You're registered, {name}!\n"
    "You'll get a message here when membership fee collection opens. "
    "Check /status at any time."
)
RELINKED = (
    "Welcome back, {name}! This Telegram account is now linked to your "
    "membership, and your payment history has moved over. Check /status."
)
RELINK_EXPIRED = (
    "This relink is no longer active. Ask the treasurer to run /relink again, "
    "then re-register with /start."
)
REGISTERED_TERM_OPEN = "You're registered, {name}! Membership fee collection is open now."
NOT_IN_CLUB = (
    "Sorry, registration is only for club members. Please join the club's "
    "Telegram group first, then send /start again.\n"
    "(Competitive players on the roster can register with their SUTD ID.)"
)
REGISTRATION_FAILED = (
    "Registration could not be completed (the SUTD ID may have just been "
    "taken). Send /start to try again or contact the treasurer."
)
CANCELLED = "Registration cancelled. Send /start whenever you're ready."
NOT_REGISTERED = "You're not registered yet. Send /start to register."
HELP_TEXT = (
    "Commands:\n"
    "/start - register or see your status\n"
    "/status - see your membership and payment status\n"
    "/pay - get your personal PayNow QR\n"
    "/help - show this message\n\n"
    "After paying, send the successful-payment screenshot here."
)
ADMIN_HELP = (
    "\n\nTreasurer/admin commands:\n"
    "/newterm - open a new paying term\n"
    "/unpaid - who hasn't paid yet\n"
    "/roster - set the competitive roster (Name, SUTD ID lines)\n"
    "/setgroup rec|comp - send inside a club group chat to link it\n"
    "/stats - payment summary for the term\n"
    "/members - list registered members\n"
    "/markpaid <sutd_id> - record a cash/manual payment\n"
    "/remind - nudge unpaid members now\n"
    "/revoke <sutd_id> - remove a membership\n"
    "/addadmin <sutd_id> - make a member an admin\n"
    "/removeadmin <sutd_id> - remove an admin\n"
    "/transfertreasurer <sutd_id> - hand over the treasurer role\n"
    "/relink <sutd_id> - let a member re-register from a new Telegram account\n"
    "/settings - view or change the PayNow/verification settings "
    "and group_posts on|off"
)


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


def _status_text(conn: sqlite3.Connection, member: sqlite3.Row) -> str:
    handle = f", @{member['username']}" if member["username"] else ""
    heading = (
        f"Registered as {member['full_name']} "
        f"(SUTD ID {member['sutd_id']}{handle})."
    )
    term = db.get_active_term(conn)
    if term is None:
        return (
            heading
            + "\n\nFee collection is not open. Nothing needs to be done right now."
        )
    payment = db.get_payment_for_member_term(
        conn, member_id=member["telegram_user_id"], term_id=term["id"]
    )
    status = payment["status"].replace("_", " ").title() if payment else "Not started"
    category = payment["category"] if payment else db.member_category(conn, member)
    details = (
        f"\n\nTerm: {term['name']}\nFee: {money(db.term_fee(term, category))}"
        f" ({money(db.term_fee(term, category, with_shirt=True))} with shirt)"
        f"\nPayment: {status}"
    )
    if payment and payment["status"] == "verified":
        return heading + details + "\n\nYour receipt has been accepted."
    return heading + details + "\n\nUse /pay to get your personal payment QR."


def _refresh_username(conn: sqlite3.Connection, member: sqlite3.Row, user) -> sqlite3.Row:
    if member["username"] != user.username:
        db.update_username(conn, user.id, user.username)
        member = db.get_member(conn, user.id)
    return member


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    member = db.get_member(_db(context), update.effective_user.id)
    if member is not None:
        member = _refresh_username(_db(context), member, update.effective_user)
        if context.args and context.args[0] == "pay":  # t.me/<bot>?start=pay
            await cmd_pay(update, context)
        else:
            await update.message.reply_text(_status_text(_db(context), member))
        return ConversationHandler.END
    # Someone outside the club groups may still be on the roster, which needs
    # their SUTD ID, so they continue and on_sutd_id makes the final call.
    context.user_data["in_club_group"] = await _in_club_group(
        context, update.effective_user.id
    )
    await update.message.reply_text(WELCOME)
    return ASK_NAME


async def _in_club_group(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if no club group is configured yet, or the user is in one of them."""
    group_ids = [
        value for key in db.GROUP_KEYS.values() if (value := db.get_setting(_db(context), key))
    ]
    if not group_ids:
        return True
    for chat_id in group_ids:
        try:
            member = await context.bot.get_chat_member(int(chat_id), user_id)
        except Exception:
            # Bot removed from the group, bad stored id, network: not in it.
            log.warning("getChatMember failed for group %s", chat_id, exc_info=True)
            continue
        if member.status in IN_GROUP_STATUSES or (
            member.status == ChatMemberStatus.RESTRICTED
            and getattr(member, "is_member", False)
        ):
            return True
    return False


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
    if not context.user_data.get("in_club_group") and (
        db.member_category(_db(context), {"sutd_id": sutd_id}) != "competitive"
    ):
        # Neither in a club group nor on the roster (relinks included).
        context.user_data.clear()
        await update.message.reply_text(NOT_IN_CLUB)
        return ConversationHandler.END
    if db.get_member_by_sutd_id(_db(context), sutd_id) is not None:
        if not db.relink_armed(_db(context), sutd_id):
            await update.message.reply_text(SUTD_ID_TAKEN)
            return ASK_SUTD_ID
        context.user_data["relink"] = True
    context.user_data["sutd_id"] = sutd_id
    await update.message.reply_text(
        CONFIRM_PROMPT.format(name=context.user_data["full_name"], sutd_id=sutd_id)
    )
    return CONFIRM


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer in ("yes", "y"):
        user = update.effective_user
        name = context.user_data["full_name"]
        sutd_id = context.user_data["sutd_id"]
        try:
            if context.user_data.get("relink"):
                # Re-check at use time: the flag may have been consumed or
                # expired while this conversation sat at the confirm step.
                if not db.relink_armed(_db(context), sutd_id):
                    context.user_data.clear()
                    await update.message.reply_text(RELINK_EXPIRED)
                    return ConversationHandler.END
                db.relink_member(
                    _db(context),
                    sutd_id=sutd_id,
                    new_telegram_id=user.id,
                    full_name=name,
                    username=user.username,
                )
                db.disarm_relink(_db(context), sutd_id)
                reply = RELINKED.format(name=name)
            else:
                db.add_member(
                    _db(context),
                    telegram_user_id=user.id,
                    full_name=name,
                    sutd_id=sutd_id,
                    username=user.username,
                )
                reply = REGISTERED.format(name=name)
        except (ValueError, sqlite3.IntegrityError):
            log.exception("Registration failed for user %s", user.id)
            context.user_data.clear()
            await update.message.reply_text(REGISTRATION_FAILED)
            return ConversationHandler.END
        relinked = context.user_data.get("relink")
        context.user_data.clear()
        scheduler.request_sheet_sync(context.application)
        if relinked or db.get_active_term(_db(context)) is None:
            await update.message.reply_text(reply)
            return ConversationHandler.END
        # Registered while collection is open (usually after the term-start
        # blast went out): go straight into the pay flow.
        await update.message.reply_text(REGISTERED_TERM_OPEN.format(name=name))
        await cmd_pay(update, context)
        return ConversationHandler.END
    if answer in ("no", "n"):
        context.user_data.clear()
        await update.message.reply_text(CANCELLED)
        return ConversationHandler.END
    await update.message.reply_text("Please reply yes or no, or use /cancel.")
    return CONFIRM


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(CANCELLED)
    return ConversationHandler.END


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member = db.get_member(_db(context), update.effective_user.id)
    if member is None:
        await update.message.reply_text(NOT_REGISTERED)
        return
    member = _refresh_username(_db(context), member, update.effective_user)
    await update.message.reply_text(_status_text(_db(context), member))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = HELP_TEXT
    if db.get_role(_db(context), update.effective_user.id) in ("treasurer", "admin"):
        text += ADMIN_HELP
    await update.message.reply_text(text)


def _parse_fee_cents(value: str, *, allow_zero: bool = False) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("fee must be a number") from exc
    # is_finite() first: NaN/Infinity pass Decimal() but break the checks below.
    too_small = amount < 0 if allow_zero else amount <= 0
    if not amount.is_finite() or too_small or amount.as_tuple().exponent < -2:
        raise ValueError("fee must be positive with at most 2 decimal places")
    return int(amount * 100)


NEWTERM_USAGE = (
    "Usage: /newterm <name> <start YYYY-MM-DD> <end YYYY-MM-DD> "
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


async def cmd_newterm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if db.get_role(_db(context), update.effective_user.id) != "treasurer":
        await update.message.reply_text("Only the treasurer can create a term.")
        return
    try:
        term = db.create_term(
            _db(context),
            **parse_newterm_args(context.args),
            created_by=update.effective_user.id,
        )
    except ValueError as exc:
        await update.message.reply_text(f"Could not create term: {exc}\n\n{NEWTERM_USAGE}")
        return
    scheduler.schedule_term_jobs(context.application, _db(context), term["id"])
    await update.message.reply_text(
        f"Term created: {term['name']}\n"
        f"Dates: {term['start_date']} to {term['end_date']}\n"
        f"Deadline: {term['deadline']}\n"
        f"Competitive: {money(db.term_fee(term, 'competitive'))}"
        f" ({money(db.term_fee(term, 'competitive', with_shirt=True))} with shirt)\n"
        f"Recreational: {money(db.term_fee(term, 'recreational'))}"
        f" ({money(db.term_fee(term, 'recreational', with_shirt=True))} with shirt)\n\n"
        "Members can now use /pay while the term is active."
    )


SIZE_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(size, callback_data=f"pay:size:{size}")
            for size in db.SHIRT_SIZES[:3]
        ],
        [
            InlineKeyboardButton(size, callback_data=f"pay:size:{size}")
            for size in db.SHIRT_SIZES[3:]
        ],
    ]
)


def _open_payment(
    conn: sqlite3.Connection, user_id: int
) -> tuple[sqlite3.Row, sqlite3.Row] | str:
    """(term, payment) for a member who can pay now, else the reason they can't."""
    if db.get_member(conn, user_id) is None:
        return NOT_REGISTERED
    term = db.get_active_term(conn)
    if term is None:
        return "Fee collection is not currently open."
    payment = db.get_or_create_payment(conn, member_id=user_id, term_id=term["id"])
    if payment["status"] == "verified":
        return "Your payment for this term is already verified."
    return term, payment


def _shirt_prompt(
    conn: sqlite3.Connection, user_id: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    opened = _open_payment(conn, user_id)
    if isinstance(opened, str):
        return opened, None
    term, payment = opened
    category = payment["category"]
    if not db.offers_shirt(term, category):
        button = InlineKeyboardButton(
            f"Get my QR ({money(db.term_fee(term, category))})",
            callback_data="pay:shirt:no",
        )
        return (
            f"{term['name']} membership ({category}).",
            InlineKeyboardMarkup([[button]]),
        )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"With shirt ({money(db.term_fee(term, category, with_shirt=True))})",
                    callback_data="pay:shirt:yes",
                ),
                InlineKeyboardButton(
                    f"Without shirt ({money(db.term_fee(term, category))})",
                    callback_data="pay:shirt:no",
                ),
            ]
        ]
    )
    return (
        f"{term['name']} membership ({category}).\nDo you want the club shirt?",
        keyboard,
    )


async def cmd_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = _shirt_prompt(_db(context), update.effective_user.id)
    await update.message.reply_text(text, reply_markup=keyboard)


async def on_pay_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """[Pay now] button on blasts/reminders: same as /pay, as a new message."""
    await update.callback_query.answer()
    text, keyboard = _shirt_prompt(_db(context), update.effective_user.id)
    await context.bot.send_message(
        chat_id=update.effective_user.id, text=text, reply_markup=keyboard
    )


async def on_optout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """[Not continuing this term] button on blasts/reminders: stop the nagging."""
    query = update.callback_query
    await query.answer()
    conn = _db(context)
    user_id = update.effective_user.id
    term = db.get_term(conn, int(query.data.removeprefix("optout:")))
    if term is None or db.get_member(conn, user_id) is None:
        await query.edit_message_text("This button is no longer valid.")
        return
    payment = db.get_or_create_payment(conn, member_id=user_id, term_id=term["id"])
    if payment["status"] == "verified":
        await query.edit_message_text(
            f"Your {term['name']} payment is already verified. Nothing to change."
        )
        return
    db.opt_out(conn, payment["id"])
    await query.edit_message_text(
        f"Noted: you're not continuing for {term['name']}, so no more reminders. "
        "Changed your mind? You can still send /pay any time."
    )


async def on_pay_shirt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "pay:shirt:yes":
        await query.edit_message_text("Pick your shirt size:", reply_markup=SIZE_KEYBOARD)
        return
    await _issue_qr(update, context, shirt_size=None)


async def on_pay_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    size = update.callback_query.data.removeprefix("pay:size:")
    await _issue_qr(update, context, shirt_size=size)


async def _issue_qr(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, shirt_size: str | None
) -> None:
    """Send the member's QR at the amount matching their shirt choice."""
    conn = _db(context)
    user_id = update.effective_user.id
    opened = _open_payment(conn, user_id)
    if isinstance(opened, str):
        await update.callback_query.edit_message_text(opened)
        return
    term, payment = opened
    db.set_shirt_size(conn, payment["id"], shirt_size)
    payment = db.mark_qr_issued(conn, payment["id"])
    amount = db.term_fee(term, payment["category"], with_shirt=shirt_size is not None)
    qr = build_member_qr(
        fee_cents=amount,
        reference=payment["ref_code"],
        school=school_config(conn),
    )
    choice = f"with shirt (size {shirt_size})" if shirt_size else "without shirt"
    await update.callback_query.edit_message_text(
        f"You chose: {choice}. Changed your mind? Send /pay again."
    )
    image = io.BytesIO(qr)
    image.name = "membership-paynow.png"
    await context.bot.send_photo(
        chat_id=user_id,
        photo=image,
        caption=(
            f"{term['name']} membership fee: {money(amount)}\n"
            "Pay using this QR, then send the successful-payment screenshot here. "
            "Expand the transfer details so the amount, recipient, Billing ID, "
            "payment time, and bank reference number are visible."
        ),
    )


def _receipt_file(message) -> tuple[str, str, int | None] | None:
    if message.photo:
        photo = message.photo[-1]
        return photo.file_id, "image/jpeg", photo.file_size
    document = message.document
    if document and document.mime_type and document.mime_type.startswith("image/"):
        return document.file_id, document.mime_type, document.file_size
    return None


async def on_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    member = db.get_member(conn, update.effective_user.id)
    if member is None:
        await update.message.reply_text(NOT_REGISTERED)
        return
    term = db.get_active_term(conn)
    if term is None:
        await update.message.reply_text("There is no active fee collection.")
        return
    payment = db.get_payment_for_member_term(
        conn, member_id=member["telegram_user_id"], term_id=term["id"]
    )
    if payment is None or not payment["qr_issued_at"]:
        await update.message.reply_text(
            "Send /pay first and use the QR I provide before submitting a receipt."
        )
        return
    if payment["status"] == "verified":
        await update.message.reply_text("Your payment is already verified.")
        return
    file_info = _receipt_file(update.message)
    if file_info is None:
        await update.message.reply_text("Please send the receipt as a photo or image.")
        return
    file_id, mime_type, file_size = file_info
    if file_size and file_size > MAX_RECEIPT_BYTES:
        await update.message.reply_text("That image is too large. Please send one under 8 MB.")
        return
    extractor = context.bot_data.get("extractor")
    if extractor is None:
        await update.message.reply_text(
            "Receipt verification is not configured yet. Ask the treasurer to add the Gemini API key."
        )
        return

    telegram_file = await context.bot.get_file(file_id)
    image_bytes = bytes(await telegram_file.download_as_bytearray())
    if len(image_bytes) > MAX_RECEIPT_BYTES:
        await update.message.reply_text("That image is too large. Please send one under 8 MB.")
        return
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    if (
        payment["status"] == "pending_verification"
        and payment["last_extract_error_at"]
        and payment["image_hash"] == image_hash
    ):
        await update.message.reply_text(
            "I already have this receipt and will verify it automatically shortly."
        )
        return
    if not db.reserve_receipt_image(
        conn, payment_id=payment["id"], image_hash=image_hash
    ):
        await update.message.reply_text(
            "This exact receipt image has already been submitted and cannot be reused."
        )
        return

    db.mark_payment_pending(
        conn,
        payment["id"],
        screenshot_file_id=file_id,
        image_hash=image_hash,
    )
    await update.message.reply_text("Checking your receipt now...")
    try:
        extracted = await extractor.extract(image_bytes, mime_type)
    except Exception:
        log.exception("Receipt extraction failed for payment %s", payment["id"])
        # Stay pending with the hash reserved; the retry job re-downloads the
        # image from screenshot_file_id (in memory) and finishes the check.
        db.record_extract_failure(conn, payment["id"])
        await update.message.reply_text(
            "Receipt received. The checker is busy right now, so I'll verify "
            "it automatically shortly. No need to send it again."
        )
        return
    await _finish_receipt(
        context,
        db.get_payment(conn, payment["id"]),
        extracted,
        update.message.reply_text,
    )


async def _finish_receipt(
    context: ContextTypes.DEFAULT_TYPE,
    payment: sqlite3.Row,
    extracted,
    reply,
) -> None:
    """Everything after a successful extraction: verify, save, notify.

    Shared by fresh receipts (on_receipt) and the retry job. `payment` is a
    db.get_payment row (it carries the term's dates and fees); `reply(text)`
    messages the member.
    """
    conn = _db(context)
    image_hash = payment["image_hash"]
    transaction_id = normalise_transaction_id(extracted.transaction_id)
    duplicate_txn = False
    if transaction_id:
        duplicate_txn = not db.reserve_bank_transaction(
            conn,
            payment_id=payment["id"],
            image_hash=image_hash,
            bank_txn_id=transaction_id,
        )
    result = verify_extracted_payment(
        extracted,
        valid_amounts=db.valid_amounts(payment, payment["category"]),
        term_start=payment["start_date"],
        term_end=payment["end_date"],
        qr_issued_at=payment["qr_issued_at"],
        duplicate_transaction=duplicate_txn,
        school=school_config(conn),
    )
    if result.outcome == "retry":
        # Nothing was accepted, so free the image fingerprint for a resend.
        # (A reservation that already carries a bank reference is kept: the
        # same member's later receipt with that reference is allowed through
        # reserve_bank_transaction's own-payment check.)
        db.release_receipt_image(
            conn, payment_id=payment["id"], image_hash=image_hash
        )
        db.reset_payment_for_retry(conn, payment["id"])
        await reply(
            "I couldn't verify that image:\n- "
            + "\n- ".join(result.reasons)
            + "\n\nPlease send a clear completed-payment screenshot."
        )
        return

    status = "verified" if result.passed else "exception"
    db.save_verification_result(
        conn,
        payment["id"],
        status=status,
        amount_cents=extracted.amount_cents,
        extracted_json=extracted.to_json(),
        # A duplicated ID remains visible in extracted_json, but cannot be put
        # in this UNIQUE column because the original submission owns it.
        bank_txn_id=None if duplicate_txn else transaction_id,
        payment_timestamp=extracted.payment_timestamp,
        verified_by="auto" if result.passed else None,
    )
    scheduler.request_sheet_sync(context.application)
    if result.passed:
        await reply(
            f"Payment receipt accepted.\n\n{payment['term_name']}\n"
            f"Amount: {money(extracted.amount_cents)}\nStatus: Verified"
        )
        return

    await reply(
        "Your receipt needs treasurer review. You will be notified after it is checked."
    )
    try:
        await _notify_treasurer(context, payment["id"], result.reasons)
    except Exception:
        # The payment stays in 'exception' (visible in /stats); don't crash the
        # handler just because the treasurer DM failed.
        log.exception("Treasurer notification failed for payment %s", payment["id"])


async def retry_failed_extractions(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retry receipts whose Gemini extraction failed (run by the JobQueue).

    The image is re-downloaded from the stored Telegram file_id into memory
    only. After MAX_EXTRACT_ATTEMPTS failures the payment goes to the
    treasurer's Approve/Reject review. Never raises.
    """
    conn = _db(context)
    extractor = context.bot_data.get("extractor")
    if extractor is None:
        return
    for payment in db.list_extraction_retries(conn, MAX_EXTRACT_ATTEMPTS):
        try:
            await _retry_one(context, extractor, payment)
        except Exception:
            log.exception("Extraction retry failed for payment %s", payment["id"])


async def _retry_one(context, extractor, payment: sqlite3.Row) -> None:
    conn = _db(context)
    member_id = payment["telegram_user_id"]

    async def reply(text: str) -> None:
        await context.bot.send_message(chat_id=member_id, text=text)

    try:
        telegram_file = await context.bot.get_file(payment["screenshot_file_id"])
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        mime_type = mimetypes.guess_type(telegram_file.file_path or "")[0]
        extracted = await extractor.extract(image_bytes, mime_type or "image/jpeg")
    except Exception:
        log.warning("Extraction retry failed for payment %s", payment["id"], exc_info=True)
        if not _still_waiting(conn, payment):
            return
        if db.record_extract_failure(conn, payment["id"]) < MAX_EXTRACT_ATTEMPTS:
            return
        # Out of retries: a human checks it. Moving to 'exception' also stops
        # further retries, so the treasurer is told exactly once.
        db.mark_payment_exception(conn, payment["id"])
        scheduler.request_sheet_sync(context.application)
        try:
            await reply(
                "I still couldn't check your receipt automatically, so the "
                "treasurer will check it by hand. You'll be notified once it's done."
            )
        except Exception:
            log.warning("Could not tell member about payment %s", payment["id"], exc_info=True)
        try:
            await context.bot.send_photo(
                chat_id=db.get_treasurer_id(conn),
                photo=payment["screenshot_file_id"],
                caption=f"Receipt from {payment['full_name']} (automatic check failed)",
            )
        except Exception:
            log.warning("Could not forward receipt %s", payment["id"], exc_info=True)
        try:
            await _notify_treasurer(
                context,
                payment["id"],
                (f"automatic receipt check failed {MAX_EXTRACT_ATTEMPTS} times; "
                 "check the screenshot above",),
            )
        except Exception:
            log.exception("Treasurer notification failed for payment %s", payment["id"])
        return
    if _still_waiting(conn, payment):
        await _finish_receipt(
            context, db.get_payment(conn, payment["id"]), extracted, reply
        )


def _still_waiting(conn: sqlite3.Connection, payment: sqlite3.Row) -> bool:
    """False if the member sent a new receipt while this retry was running."""
    current = db.get_payment(conn, payment["id"])
    return (
        current["status"] == "pending_verification"
        and current["last_extract_error_at"] is not None
        and current["screenshot_file_id"] == payment["screenshot_file_id"]
    )


async def _notify_treasurer(
    context: ContextTypes.DEFAULT_TYPE, payment_id: int, reasons: tuple[str, ...]
) -> None:
    conn = _db(context)
    payment = db.get_payment(conn, payment_id)
    row = conn.execute(
        "SELECT telegram_user_id FROM admins WHERE role = 'treasurer'"
    ).fetchone()
    if payment is None or row is None:
        log.error("Cannot notify treasurer for payment %s", payment_id)
        return
    expected = " or ".join(
        money(cents)
        for cents in sorted(db.valid_amounts(payment, payment["category"]))
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Approve", callback_data=f"payment:approve:{payment_id}"
                ),
                InlineKeyboardButton(
                    "Reject", callback_data=f"payment:reject:{payment_id}"
                ),
            ]
        ]
    )
    await context.bot.send_message(
        chat_id=row["telegram_user_id"],
        text=(
            f"Payment exception\n\nMember: {payment['full_name']}\n"
            f"SUTD ID: {payment['sutd_id']}\nTerm: {payment['term_name']}\n"
            f"Expected: {expected} ({payment['category']})\n\nReasons:\n- "
            + "\n- ".join(reasons)
        ),
        reply_markup=keyboard,
    )


async def on_payment_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if db.get_role(_db(context), update.effective_user.id) != "treasurer":
        await query.edit_message_text("Only the treasurer can review payments.")
        return
    try:
        _, action, raw_id = query.data.split(":")
        payment_id = int(raw_id)
        if action not in {"approve", "reject"}:
            raise ValueError
        payment = db.review_payment(
            _db(context), payment_id, approve=action == "approve"
        )
    except (ValueError, TypeError):
        await query.edit_message_text(
            "This review action is invalid or has already been completed."
        )
        return
    approved = action == "approve"
    scheduler.request_sheet_sync(context.application)
    await query.edit_message_text(
        f"Payment {'approved' if approved else 'rejected'} for "
        f"{payment['full_name']} ({payment['term_name']})."
    )
    await context.bot.send_message(
        chat_id=payment["telegram_user_id"],
        text=(
            f"Your payment for {payment['term_name']} was "
            f"{'approved' if approved else 'rejected by the treasurer. Please contact the treasurer or submit a new receipt'}."
        ),
    )


async def _ignore_edited(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.edited_message is not None or update.edited_channel_post is not None:
        raise ApplicationHandlerStop


PRIVATE = filters.ChatType.PRIVATE


def build_application(
    token: str,
    conn: sqlite3.Connection,
    *,
    extractor: Any | None = None,
    sheet: Any | None = None,
) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["db"] = conn
    app.bot_data["extractor"] = extractor
    app.bot_data["sheet"] = sheet
    # Every handler dereferences update.message; edited messages would arrive
    # with message=None and crash them, so drop edits before any other group.
    app.add_handler(TypeHandler(Update, _ignore_edited), group=-1)
    # The bot also sits in the club groups (/setgroup, progress posts): keep
    # registration, payments, receipts and admin output out of them.
    registration = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start, filters=PRIVATE)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_name)],
            ASK_SUTD_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_sutd_id)
            ],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(registration)
    app.add_handler(CommandHandler("status", cmd_status, filters=PRIVATE))
    app.add_handler(CommandHandler("help", cmd_help, filters=PRIVATE))
    app.add_handler(CommandHandler("newterm", cmd_newterm, filters=PRIVATE))
    app.add_handler(CommandHandler("pay", cmd_pay, filters=PRIVATE))
    app.add_handler(CommandHandler("unpaid", admin.cmd_unpaid, filters=PRIVATE))
    app.add_handler(CommandHandler("stats", admin.cmd_stats, filters=PRIVATE))
    app.add_handler(CommandHandler("members", admin.cmd_members, filters=PRIVATE))
    app.add_handler(CommandHandler("markpaid", admin.cmd_markpaid, filters=PRIVATE))
    app.add_handler(CommandHandler("remind", admin.cmd_remind, filters=PRIVATE))
    app.add_handler(CommandHandler("revoke", admin.cmd_revoke, filters=PRIVATE))
    app.add_handler(CommandHandler("addadmin", admin.cmd_addadmin, filters=PRIVATE))
    app.add_handler(CommandHandler("removeadmin", admin.cmd_removeadmin, filters=PRIVATE))
    app.add_handler(CommandHandler("transfertreasurer", admin.cmd_transfertreasurer, filters=PRIVATE))
    app.add_handler(CommandHandler("relink", admin.cmd_relink, filters=PRIVATE))
    app.add_handler(CommandHandler("settings", admin.cmd_settings, filters=PRIVATE))
    app.add_handler(CommandHandler("roster", admin.cmd_roster, filters=PRIVATE))
    app.add_handler(CommandHandler("setgroup", admin.cmd_setgroup))
    app.add_handler(CallbackQueryHandler(on_pay_start, pattern=r"^pay:start$"))
    app.add_handler(CallbackQueryHandler(on_optout, pattern=r"^optout:\d+$"))
    app.add_handler(
        CallbackQueryHandler(on_pay_shirt, pattern=r"^pay:shirt:(yes|no)$")
    )
    app.add_handler(
        CallbackQueryHandler(on_pay_size, pattern=r"^pay:size:(XS|S|M|L|XL|XXL)$")
    )
    app.add_handler(
        CallbackQueryHandler(on_payment_review, pattern=r"^payment:(approve|reject):\d+$")
    )
    app.add_handler(
        MessageHandler(PRIVATE & (filters.PHOTO | filters.Document.IMAGE), on_receipt)
    )
    if app.job_queue is not None:
        scheduler.schedule_all(app, conn)
    else:
        log.warning(
            "JobQueue unavailable (install python-telegram-bot[job-queue]); "
            "term-start blasts, reminders, and backups are disabled."
        )
    return app
