"""Remove unpaid people from the REC group chat. The comp chat is never touched.

Rule (docs/superpowers/specs/removing-member-plan.md): during an open term,
someone in the rec chat is removed once their date (the later of the term
deadline and the day the bot first saw them, plus the grace days) has passed,
unless they paid, have a receipt being checked, are an admin, or are on the
Keep list. The treasurer gets a preview two days ahead; nothing is removed
until `/settings remove_unpaid on`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from clubbot import db
from clubbot.format import money

log = logging.getLogger(__name__)

REC_KEY = db.GROUP_KEYS["rec"]
JOIN_LINK_KEY = "rec_join_link"
MODES = ("off", "preview", "on")
DEFAULT_MODE = "preview"  # first term: the treasurer sees the list, nobody is removed
DEFAULT_GRACE_DAYS = 7
MAX_GRACE_DAYS = 60
PREVIEW_LEAD = timedelta(days=2)  # preview + final warning this long before removal
KICK_PAUSE = 1.0  # seconds between removals; Telegram documents no ban rate limit
# A receipt being checked keeps its sender: a Gemini outage or a slow review
# must never get anyone removed.
SPARED_STATUSES = ("verified", "pending_verification", "exception")
KEEP_BUTTONS = 30  # same cap as /unpaid's Mark-paid buttons
MESSAGE_LIMIT = 4096
MESSAGE_MARGIN = 3500  # leave room for headings and final explanatory notes
PAY_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("Pay now", callback_data="pay:start")]]
)


def mode(conn: sqlite3.Connection) -> str:
    value = db.get_setting(conn, "remove_unpaid")
    return value if value in MODES else DEFAULT_MODE


def grace_days(conn: sqlite3.Connection) -> int:
    value = db.get_setting(conn, "removal_grace_days")
    return int(value) if value and value.isdigit() else DEFAULT_GRACE_DAYS


def removal_day(conn: sqlite3.Connection, term) -> date:
    """When people present since the start of the term get removed if unpaid."""
    return date.fromisoformat(term["deadline"]) + timedelta(days=grace_days(conn))


def due_date(conn: sqlite3.Connection, term, first_seen_at: str | None) -> date:
    """A late joiner gets the grace period from the day the bot first saw them."""
    start = date.fromisoformat(term["deadline"])
    if first_seen_at:
        seen = datetime.fromisoformat(first_seen_at).astimezone(db.SINGAPORE_TIME).date()
        start = max(start, seen)
    return start + timedelta(days=grace_days(conn))


def is_overdue(conn: sqlite3.Connection, term, person, day: date) -> bool:
    """Should `person` (a list_rec_candidates/get_rec_person row) be out by `day`?"""
    user_id = person["telegram_user_id"]
    if person["keep"] or db.get_role(conn, user_id):
        return False
    payment = db.get_payment_for_member_term(conn, member_id=user_id, term_id=term["id"])
    if payment is not None and payment["status"] in SPARED_STATUSES:
        return False
    return due_date(conn, term, person["first_seen_at"]) <= day


def in_chat(chat_member) -> bool:
    """getChatMember result → is this person currently in the chat?"""
    return chat_member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    ) or (
        chat_member.status == ChatMemberStatus.RESTRICTED
        and getattr(chat_member, "is_member", False)
    )


def removal_notice(conn: sqlite3.Connection, term) -> str:
    """Line added to reminders and the rec group post while removal is on."""
    if mode(conn) != "on" or db.get_setting(conn, REC_KEY) is None:
        return ""
    return (
        "\nUnpaid members are removed from the rec group chat on "
        f"{removal_day(conn, term).isoformat()}."
    )


def _rec_chat(conn: sqlite3.Connection) -> int | None:
    value = db.get_setting(conn, REC_KEY)
    return int(value) if value else None


def _who(person) -> str:
    if person["sutd_id"]:
        return f"{person['name']} (SUTD ID {person['sutd_id']})"
    return f"{person['name']} (not registered with the bot)"


async def _send(bot, chat_id: int, text: str, reply_markup=None) -> bool:
    """True = done (sent, or they blocked/never started the bot); False = retry."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except Forbidden:
        return True
    except TelegramError:
        log.warning("Message to %s failed; will retry", chat_id, exc_info=True)
        return False
    return True


async def overdue_in_chat(bot, conn, term, chat_id: int, day: date):
    """(people in the chat who should be out by `day`, how many people in the
    chat the bot can identify). One getChatMember per known person."""
    overdue, identified = [], 0
    for person in db.list_rec_candidates(conn):
        try:
            member = await bot.get_chat_member(chat_id, person["telegram_user_id"])
        except TelegramError:
            continue  # e.g. "user not found": never in this chat
        if not in_chat(member):
            continue
        identified += 1
        if member.user.is_bot or member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            continue  # Telegram admins are never removed
        if is_overdue(conn, term, person, day):
            overdue.append(person)
    return overdue, identified


def _preview_groups(people) -> list[list]:
    """Batches small enough for Telegram text and inline-keyboard limits."""
    groups, current, chars = [], [], 0
    for person in people:
        line = f"- {_who(person)}"
        if current and (
            len(current) >= KEEP_BUTTONS or chars + len(line) + 1 > MESSAGE_MARGIN
        ):
            groups.append(current)
            current, chars = [], 0
        current.append(person)
        chars += len(line) + 1
    if current:
        groups.append(current)
    return groups


async def _send_summary(bot, chat_id: int, heading: str, lines: list[str]) -> None:
    """Send a long result in safe chunks; summaries are best effort."""
    chunks, current = [], heading
    for line in lines:
        line = line[: MESSAGE_LIMIT - 100]
        candidate = f"{current}\n{line}"
        if len(candidate) > MESSAGE_LIMIT:
            chunks.append(current)
            current = f"{heading} (continued)\n{line}"
        else:
            current = candidate
    chunks.append(current)
    for chunk in chunks:
        await _send(bot, chat_id, chunk)


async def send_preview(
    bot,
    conn,
    term,
    chat_id: int,
    *,
    target_day: date | None = None,
    include_empty: bool = True,
) -> bool:
    """Preview everyone due by target_day and warn each registered member."""
    day = target_day or removal_day(conn, term)
    people, identified = await overdue_in_chat(bot, conn, term, chat_id, day)
    live = mode(conn) == "on"
    if live:
        people = [
            person
            for person in people
            if not db.term_event_claimed(
                conn, term["id"], f"removal-previewed:{person['telegram_user_id']}"
            )
        ]
    if not people and not include_empty:
        return True

    try:
        total = await bot.get_chat_member_count(chat_id)
    except TelegramError:
        total = None

    # A transient DM failure must not silently consume the person's warning.
    if live:
        for person in people:
            stamp = f"removal-warning:{person['telegram_user_id']}"
            if not person["sutd_id"] or db.term_event_claimed(conn, term["id"], stamp):
                continue  # the bot can only DM people who started it
            warning = (
                f"Your {term['name']} membership is still unpaid. Unpaid members are "
                "removed from the rec group chat on "
                f"{due_date(conn, term, person['first_seen_at']).isoformat()}. "
                "Tap Pay now to get your QR."
            )
            if await _send(bot, person["telegram_user_id"], warning, PAY_KEYBOARD):
                db.claim_term_event(conn, term["id"], stamp)
            else:
                return False

    groups = _preview_groups(people) or [[]]
    exceptions = db.get_term_payment_stats(conn, term["id"])["exceptions"]
    for index, group in enumerate(groups):
        lines = [
            f"Rec chat clean-up for {term['name']}"
            + (f" ({index + 1}/{len(groups)})" if len(groups) > 1 else "")
            + f": on {day.isoformat()} I "
            + ("will remove" if live else "would remove")
            + " these people if they still haven't paid:"
        ]
        lines += [f"- {_who(person)}" for person in group] or ["(nobody right now)"]
        if group:
            lines.append("Tap Keep for anyone who should never be removed (coaches etc.).")
        if index == len(groups) - 1:
            if not live:
                lines.append(
                    "\nPREVIEW MODE: nobody will actually be removed. "
                    "Turn it on with /settings remove_unpaid on"
                )
            if exceptions:
                lines.append(
                    f"\n{exceptions} receipt(s) wait for your review; those people stay."
                )
            if total is not None:
                lines.append(
                    f"\nThe chat has {total} members; I can identify {identified} of them "
                    "(bots and people who never joined while I was watching are invisible to me)."
                )
        keyboard = (
            InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(
                        f"Keep: {person['name']}"[:60],
                        callback_data=f"kp:{person['telegram_user_id']}",
                    )]
                    for person in group
                ]
            )
            if group
            else None
        )
        text = "\n".join(lines)
        if len(text) > MESSAGE_LIMIT:
            log.error("Removal preview chunk unexpectedly exceeds Telegram's limit")
            return False
        if not await _send(bot, db.get_treasurer_id(conn), text, keyboard):
            return False

    if live:
        for person in people:
            db.claim_term_event(
                conn, term["id"], f"removal-previewed:{person['telegram_user_id']}"
            )
    return True


async def sweep(bot, conn, term, chat_id: int, today: date) -> None:
    """Remove overdue people only after each has had the full warning window."""
    people, _ = await overdue_in_chat(bot, conn, term, chat_id, today)
    warned = []
    for person in people:
        previewed = db.term_event_sent_at(
            conn, term["id"], f"removal-previewed:{person['telegram_user_id']}"
        )
        if previewed is None:
            continue
        preview_day = previewed.astimezone(db.SINGAPORE_TIME).date()
        if today >= preview_day + PREVIEW_LEAD:
            warned.append(person)

    removed, unlock_pending, failed = [], [], []
    link = db.get_setting(conn, JOIN_LINK_KEY)
    rejoin = f"ask to join again with {link}" if link else "ask to join the chat again"
    for person in warned:
        user_id = person["telegram_user_id"]
        # Queue first. If the process dies after Telegram applies the ban but
        # before this coroutine resumes, the next due-check still unbans them.
        db.queue_pending_unban(conn, chat_id, user_id, person["name"])
        try:
            await bot.ban_chat_member(chat_id, user_id)
        except (BadRequest, Forbidden) as exc:
            # Telegram definitively rejected these requests, so no ban was made.
            db.clear_pending_unban(conn, chat_id, user_id)
            failed.append(f"- {_who(person)}: {exc}")
            continue
        except TelegramError as exc:
            # Network failures are ambiguous: leave recovery queued in case
            # Telegram applied the request before the response was lost.
            failed.append(f"- {_who(person)}: {exc}")
            continue

        try:
            await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        except TelegramError as exc:
            unlock_pending.append(f"- {_who(person)}: {exc}")
        else:
            db.clear_pending_unban(conn, chat_id, user_id)
        removed.append(f"- {_who(person)}")
        if person["sutd_id"]:
            await _send(
                bot,
                user_id,
                f"You've been removed from the rec group chat because your "
                f"{term['name']} membership wasn't paid. Pay with /pay; once "
                f"it's verified, {rejoin} and you'll be let in automatically. "
                "Already paid? Contact the treasurer.",
            )
        await asyncio.sleep(KICK_PAUSE)
    if not removed and not failed:
        return
    lines = []
    if removed:
        lines += [f"Removed {len(removed)}:"] + removed
    if unlock_pending:
        lines += [
            f"Rejoin unlock pending for {len(unlock_pending)} (I'll retry hourly):"
        ] + unlock_pending
    if failed:
        lines += [f"Could not remove {len(failed)} (I'll try again tomorrow):"] + failed
    await _send_summary(
        bot, db.get_treasurer_id(conn), f"Rec chat clean-up for {term['name']}:", lines
    )


async def retry_pending_unbans(bot, conn, chat_id: int) -> None:
    """Finish interrupted kicks before doing any new removal work."""
    for pending in db.list_pending_unbans(conn, chat_id):
        try:
            await bot.unban_chat_member(
                chat_id, pending["telegram_user_id"], only_if_banned=True
            )
        except TelegramError:
            log.warning(
                "Still unable to unban %s in %s",
                pending["telegram_user_id"],
                chat_id,
                exc_info=True,
            )
        else:
            db.clear_pending_unban(conn, chat_id, pending["telegram_user_id"])


async def run_due(bot, conn: sqlite3.Connection, term, today: date) -> None:
    """Called by the hourly due-check for each term that is open today."""
    chat_id = _rec_chat(conn)
    current = mode(conn)
    if chat_id is None:
        return
    await retry_pending_unbans(bot, conn, chat_id)
    if current == "off" or db.get_treasurer_id(conn) is None:
        return
    day = removal_day(conn, term)
    if current != "on":
        event = "removal-preview-dry"
        if today >= day - PREVIEW_LEAD and not db.term_event_claimed(
            conn, term["id"], event
        ):
            if await send_preview(bot, conn, term, chat_id):
                db.claim_term_event(conn, term["id"], event)
        return

    # Look two days ahead every day. Late joiners therefore receive their own
    # preview and final warning instead of relying on the term's global preview.
    if today >= day - PREVIEW_LEAD:
        target = today + PREVIEW_LEAD
        event = f"removal-preview:{target.isoformat()}"
        if not db.term_event_claimed(conn, term["id"], event):
            if await send_preview(
                bot,
                conn,
                term,
                chat_id,
                target_day=target,
                include_empty=target == day,
            ):
                db.claim_term_event(conn, term["id"], event)

    sweep_event = f"sweep-{today.isoformat()}"
    if today < day or db.term_event_claimed(conn, term["id"], sweep_event):
        return
    await sweep(bot, conn, term, chat_id, today)
    # Stamped even after failures: ban failures are retried tomorrow, not hourly.
    db.claim_term_event(conn, term["id"], sweep_event)


# --- Telegram handlers ----------------------------------------------------------


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


def _is_rec_chat(conn: sqlite3.Connection, chat_id: int) -> bool:
    return _rec_chat(conn) == chat_id


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Note everyone who joins the rec chat (needs allowed_updates chat_member)."""
    change = update.chat_member
    conn = _db(context)
    if not _is_rec_chat(conn, change.chat.id):
        return  # the comp chat (or any other) is never tracked
    user = change.new_chat_member.user
    if in_chat(change.new_chat_member) and not user.is_bot:
        db.note_rec_person(conn, user.id, user.full_name)


async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Join-request link of the rec chat: welcome DM first, then approve, or
    decline someone whose date passed unpaid."""
    request = update.chat_join_request
    conn = _db(context)
    if not _is_rec_chat(conn, request.chat.id):
        return  # leave other chats' requests to their human admins
    user = request.from_user
    db.note_rec_person(conn, user.id, user.full_name)
    term = db.get_active_term(conn)
    today = datetime.now(db.SINGAPORE_TIME).date()
    person = db.get_rec_person(conn, user.id)
    let_in = (
        term is None
        or mode(conn) != "on"
        or not is_overdue(conn, term, person, today)
    )
    if term is None:
        text = (
            "Welcome to the SUTD Badminton rec chat! Membership fee collection "
            "isn't open right now. Send /start here to register, and I'll "
            "message you when it opens."
        )
    elif let_in:
        text = (
            f"Welcome to the SUTD Badminton rec chat! {term['name']} membership "
            f"is {money(term['rec_fee_cents'])} "
            f"({money(term['recshirt_fee_cents'])} with the club shirt). "
            "Send /start here to register and pay."
        )
        if mode(conn) == "on":
            text += (
                " Unpaid members are removed from the chat on "
                f"{due_date(conn, term, person['first_seen_at']).isoformat()}."
            )
    else:
        text = (
            f"Your {term['name']} membership isn't paid yet, so I can't let you "
            "into the rec chat. Send /start here to register (or /pay if you "
            "already have), pay, then ask to join again: you'll be let in "
            "automatically."
        )
    # The DM window (user_chat_id) closes once the request is handled: DM first.
    try:
        await context.bot.send_message(chat_id=request.user_chat_id, text=text)
    except TelegramError:
        log.warning("Could not DM join requester %s", user.id, exc_info=True)
    try:
        if let_in:
            await request.approve()
        else:
            await request.decline()
    except TelegramError:
        log.warning("Could not handle join request from %s", user.id, exc_info=True)


def _person_name(conn: sqlite3.Connection, user_id: int) -> str:
    member = db.get_member(conn, user_id)
    if member is not None:
        return member["full_name"]
    person = db.get_rec_person(conn, user_id)
    return person["name"] if person else str(user_id)


async def cmd_keep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Treasurer: list the Keep list, with a button to take each person off."""
    conn = _db(context)
    if db.get_role(conn, update.effective_user.id) != "treasurer":
        await update.message.reply_text("Only the treasurer can use this command.")
        return
    kept = db.list_rec_kept(conn)
    if not kept:
        await update.message.reply_text(
            "Nobody is on the Keep list. Keep buttons appear in the rec chat "
            "clean-up preview the bot sends you before removal day."
        )
        return
    await update.message.reply_text(
        "Never removed from the rec chat (tap to take someone off the list):",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Unkeep: {k['name']}"[:60],
                        callback_data=f"uk:{k['telegram_user_id']}",
                    )
                ]
                for k in kept[:KEEP_BUTTONS]
            ]
        ),
    )


async def on_keep_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """[Keep: X] in the preview (kp:) or [Unkeep: X] in /keep (uk:)."""
    query = update.callback_query
    conn = _db(context)
    if db.get_role(conn, update.effective_user.id) != "treasurer":
        await query.answer("Only the treasurer can change the Keep list.")
        return
    action, raw_id = query.data.split(":")
    user_id = int(raw_id)
    name = _person_name(conn, user_id)
    keep = action == "kp"
    db.set_rec_keep(conn, user_id, name, keep)
    await query.answer(f"{name} will {'never' if keep else 'now'} be removed if unpaid.")
    rows = [
        row
        for row in query.message.reply_markup.inline_keyboard
        if row[0].callback_data != query.data
    ]
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(rows) if rows else None
    )
