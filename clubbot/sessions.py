"""Recre session sign-ups (replaces Mitup) and the nets & shuttles pick.

An admin DMs /session; the session is saved as a draft and only reaches the
rec group on [Post]. Sign-ups are ordered by join id: the first `capacity`
play, the rest wait, so a leaver's spot passes on with no extra bookkeeping.
At noon on the session day the bot picks a primary (collects the nets and
shuttles from the store room) and a secondary (only if the primary can't).
Plan: docs/superpowers/specs/2026-10-10-session-signups-plan.md
"""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import sqlite3
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.helpers import mention_html

from clubbot import db, newterm
from clubbot.db import SINGAPORE_TIME

log = logging.getLogger(__name__)

PICK_HOUR = 12           # noon SGT on the session day (the day before if it starts by 2pm)
PICK_NOTICE = timedelta(hours=2)
MAX_SPOTS = 60           # keeps the card far under Telegram's 4096 characters
WAITLIST_SHOWN = 20
NAME_CHARS = 32
CARD_EDIT_DELAY = 1.5    # seconds; bundles a rush of taps into one edit
DEFAULT_TEMPLATE = ("Badminton Recre🏸", "7pm-11pm", "ISH 2", 30)

NOT_ADMIN = "This command is for club admins."
NO_REC_GROUP = (
    "No rec group is linked yet. Add me to the rec group as an admin and send "
    "/setgroup rec there first."
)
USAGE = (
    "Send /session followed by 5 lines: title, date, time, place, spots.\n"
    "Dates: 16 Oct, Thu 16 Oct, 16/10, 2026-10-16. Times: 7pm-11pm, 7-11pm, 19:00-23:00."
)


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


def _is_admin(conn: sqlite3.Connection, uid: int) -> bool:
    return db.get_role(conn, uid) in ("treasurer", "admin")


def _now() -> datetime:
    return datetime.now(SINGAPORE_TIME)


# --- Parsing ------------------------------------------------------------------

_CLOCK = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?")
_WEEKDAY = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", re.I)


def _clock(text: str, suffix: str | None) -> time:
    match = _CLOCK.fullmatch(text.strip())
    if not match:
        raise ValueError(f"can't read the time '{text}'")
    hour, minute = int(match[1]), int(match[2] or 0)
    suffix = match[3] or suffix
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError(f"can't read the time '{text}'")
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    return time(hour, minute)


def parse_times(text: str) -> tuple[time, time]:
    """'7pm-11pm', '7-11pm', '7:30pm-10pm', '19:00-23:00'."""
    parts = re.split(r"\s*[-–—]\s*|\s+to\s+", text.strip().lower())
    if len(parts) != 2:
        raise ValueError("the time should look like 7pm-11pm")
    suffixes = [re.search(r"(am|pm)$", part) for part in parts]
    start = _clock(parts[0], suffixes[1] and suffixes[1][1])
    end = _clock(parts[1], suffixes[0] and suffixes[0][1])
    if end <= start:
        raise ValueError("the end time must be after the start time")
    return start, end


def parse_day(text: str, today: date) -> date:
    """A full date, or '16 Oct' / 'Thu 16 Oct' / '16/10' meaning the next one."""
    text = _WEEKDAY.sub("", text.strip())
    try:
        return newterm.parse_date(text)
    except ValueError:
        pass
    for fmt in ("%d %b", "%d %B", "%d/%m"):
        try:
            day = datetime.strptime(f"{text} {today.year}", f"{fmt} %Y").date()
        except ValueError:
            continue
        return day if day >= today else day.replace(year=today.year + 1)
    raise ValueError(f"can't read the date '{text}'")


def parse_session(text: str, now: datetime) -> dict:
    """The 5 lines after /session -> create_session kwargs (minus host)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 5:
        raise ValueError("I need exactly 5 lines: title, date, time, place, spots")
    title, day_text, time_text, venue, spots = lines
    day = parse_day(day_text, now.date())
    start, end = parse_times(time_text)
    if not spots.isdigit() or not 1 <= int(spots) <= MAX_SPOTS:
        raise ValueError(f"spots must be a number from 1 to {MAX_SPOTS}")
    starts_at = datetime.combine(day, start, SINGAPORE_TIME)
    if starts_at <= now:
        raise ValueError("that session has already started")
    if len(title) > 100 or len(venue) > 100:
        raise ValueError("keep the title and place under 100 characters")
    return {
        "title": title,
        "starts_at": starts_at.isoformat(),
        "ends_at": datetime.combine(day, end, SINGAPORE_TIME).isoformat(),
        "venue": venue,
        "capacity": int(spots),
    }


# --- Rendering ----------------------------------------------------------------


def _hm(t: datetime) -> str:
    hour = t.hour % 12 or 12
    minutes = f":{t:%M}" if t.minute else ""
    return f"{hour}{minutes}{'am' if t.hour < 12 else 'pm'}"


def _start(session) -> datetime:
    return datetime.fromisoformat(session["starts_at"])


def when_text(session) -> str:
    start, end = _start(session), datetime.fromisoformat(session["ends_at"])
    return f"{start:%a} {start.day} {start:%b} · {_hm(start)}–{_hm(end)}"


def card_text(session, signups) -> str:
    cap = session["capacity"]
    players, waiting = signups[:cap], signups[cap:]
    names = {s["user_id"]: s["name"] for s in signups}
    lines = [
        session["title"],
        when_text(session),
        f"📍 {session['venue']}",
        f"Hosted by: {session['host_name']}",
        "",
        f"👥 Participants · {len(players)} of {cap}",
    ]
    lines += [f"{i}. {s['name']}" for i, s in enumerate(players, 1)]
    if waiting:
        lines += ["", f"⏳ Waitlist · {len(waiting)}"]
        lines += [f"{i}. {s['name']}" for i, s in enumerate(waiting[:WAITLIST_SHOWN], 1)]
        if len(waiting) > WAITLIST_SHOWN:
            lines.append(f"…and {len(waiting) - WAITLIST_SHOWN} more")
    if session["primary_id"] in names:
        line = f"🧺 Nets & shuttles: {names[session['primary_id']]}"
        if session["secondary_id"] in names:
            line += f" (backup: {names[session['secondary_id']]})"
        lines += ["", line]
    if session["cancelled_at"]:
        lines = ["❌ CANCELLED", ""] + lines
    return "\n".join(lines)


def card_keyboard(session) -> InlineKeyboardMarkup | None:
    if session["cancelled_at"]:
        return None
    sid = session["id"]
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Join", callback_data=f"ss:j:{sid}"),
            InlineKeyboardButton("❌ Leave", callback_data=f"ss:l:{sid}"),
        ]]
    )


def _template(session, now: datetime) -> str:
    """The 5 lines for the next weekly repeat of `session` (or a default)."""
    if session is None:
        title, times, venue, spots = DEFAULT_TEMPLATE
        day = now.date() + timedelta(days=7)
    else:
        start = _start(session)
        title, venue, spots = session["title"], session["venue"], session["capacity"]
        times = f"{_hm(start)}-{_hm(datetime.fromisoformat(session['ends_at']))}"
        day = start.date()
        while datetime.combine(day, start.timetz()) <= now:
            day += timedelta(days=7)
    return f"{title}\n{day:%a} {day.day} {day:%b}\n{times}\n{venue}\n{spots}"


def _preview(session) -> tuple[str, InlineKeyboardMarkup]:
    sid = session["id"]
    return (
        "Preview: this is what the rec group will see.\n\n" + card_text(session, []),
        InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("📣 Post to rec group", callback_data=f"ss:p:{sid}"),
                InlineKeyboardButton("Cancel", callback_data=f"ss:x:{sid}"),
            ]]
        ),
    )


def _posted_keyboard(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel session", callback_data=f"ss:c:{sid}")]]
    )


# --- The pick -----------------------------------------------------------------


def choose(players: list[int], counts: dict[int, int], k: int, rng=random) -> list[int]:
    """Up to k different people, each from those picked fewest times, random among ties."""
    pool, chosen = list(players), []
    for _ in range(min(k, len(pool))):
        fewest = min(counts.get(p, 0) for p in pool)
        person = rng.choice([p for p in pool if counts.get(p, 0) == fewest])
        chosen.append(person)
        pool.remove(person)
    return chosen


def _player_ids(conn: sqlite3.Connection, session) -> list[int]:
    return [s["user_id"] for s in db.list_signups(conn, session["id"])[: session["capacity"]]]


def _fill_roles(conn: sqlite3.Connection, session, keep: list[int]) -> tuple[int | None, int | None]:
    """Keep `keep` (in order) and top up to a primary + secondary from the players."""
    others = [p for p in _player_ids(conn, session) if p not in keep]
    new = choose(others, db.times_primary(conn, session["id"]), 2 - len(keep))
    primary, secondary = (keep + new + [None, None])[:2]
    return primary, secondary


def _names(conn: sqlite3.Connection, session) -> dict[int, str]:
    return {s["user_id"]: s["name"] for s in db.list_signups(conn, session["id"])}


def _role_dm(session, role: str, primary_name: str = "") -> str:
    where = f"{session['title']}, {when_text(session)} at {session['venue']}"
    if role == "primary":
        return (
            f"🧺 You're collecting the nets and shuttles for {where}.\n"
            "Please get them from the store room before the session starts.\n"
            "Can't make it? Tap below and your backup takes over."
        )
    return (
        f"🧺 You're the backup for the nets and shuttles for {where}.\n"
        f"Only needed if {primary_name} can't make it; I'll message you if that happens."
    )


def _cant_make_it(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ I can't make it", callback_data=f"ss:l:{sid}")]]
    )


async def _dm(bot, user_id: int, text: str, reply_markup=None) -> bool:
    """Best effort: people who never started the bot can't be DMed (the group
    mention still reaches them)."""
    try:
        await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    except TelegramError as exc:
        log.info("Could not DM %s: %s", user_id, exc)
        return False
    return True


async def _dm_roles(bot, session, names, *, primary_changed=True, secondary_changed=True):
    primary, secondary = session["primary_id"], session["secondary_id"]
    if primary and primary_changed:
        await _dm(bot, primary, _role_dm(session, "primary"), _cant_make_it(session["id"]))
    if secondary and secondary_changed:
        await _dm(bot, secondary, _role_dm(session, "secondary", names.get(primary, "")))


async def _group_post(bot, session, text: str) -> bool:
    """HTML message to the group, as a reply to the card. True when delivered."""
    try:
        await bot.send_message(
            chat_id=session["chat_id"],
            text=text,
            parse_mode="HTML",
            reply_parameters=ReplyParameters(
                message_id=session["message_id"], allow_sending_without_reply=True
            ),
        )
    except TelegramError:
        log.warning("Group post for session %s failed", session["id"], exc_info=True)
        return False
    return True


def _pick_text(session, names) -> str:
    primary, secondary = session["primary_id"], session["secondary_id"]
    when = html.escape(when_text(session))
    text = (
        f"🧺 {when}: {mention_html(primary, names[primary])} please collect the "
        "nets and shuttles from the store room."
    )
    if secondary:
        text += (
            f"\nBackup: {mention_html(secondary, names[secondary])} "
            f"(only if {html.escape(names[primary])} can't make it)."
        )
    return text + "\nCan't make it? Tap ❌ Leave on the session post."


def pick_time(session) -> datetime:
    start = _start(session)
    noon = start.replace(hour=PICK_HOUR, minute=0, second=0)
    return noon if noon <= start - PICK_NOTICE else noon - timedelta(days=1)


async def run_due_picks(bot, conn: sqlite3.Connection, now: datetime) -> None:
    """Hourly: pick from noon on the session day; (re)send an unannounced pick.

    The pick is saved before it's announced, so a restart can't re-roll it;
    announced_at is stamped only once the group post is delivered.
    """
    for row in db.sessions_awaiting_pick_announcement(conn):
        session = db.get_session(conn, row["id"])  # fresh: earlier awaits may have changed it
        start = _start(session)
        if now >= start or now < pick_time(session):
            continue
        if session["picked_at"] is None:
            primary, secondary = _fill_roles(conn, session, [])
            if primary is None:
                if session["empty_notified_at"] is None:
                    notified = await _dm(
                        bot,
                        session["host_id"],
                        f"Nobody is signed up for {session['title']} on "
                        f"{when_text(session)} yet, so I couldn't assign anyone "
                        "to collect the nets and shuttles. I'll check again hourly.",
                    )
                    if notified:
                        db.mark_session_empty_notified(conn, session["id"])
                continue  # nobody playing yet; try again next hour
            db.set_session_pick(conn, session["id"], primary, secondary)
            session = db.get_session(conn, session["id"])
            if not await _refresh_now(bot, conn, session["id"]):
                continue
        if session["primary_id"] is None:
            # A previously announced pick can become empty after everyone
            # leaves. If that handoff post failed, announced_at was cleared;
            # retry the authoritative empty state here.
            if session["picked_at"] is not None:
                text = (
                    f"🧺 {html.escape(when_text(session))}: nobody is left to "
                    "collect the nets and shuttles."
                )
                if await _group_post(bot, session, text):
                    db.mark_session_announced(conn, session["id"])
            continue
        names = _names(conn, session)
        if await _group_post(bot, session, _pick_text(session, names)):
            db.mark_session_announced(conn, session["id"])
            await _dm_roles(bot, session, names)


# --- The card in the group ------------------------------------------------------


async def refresh_card(bot, conn: sqlite3.Connection, sid: int) -> None:
    """Re-render the group card from the DB. Raises RetryAfter (caller retries)."""
    session = db.get_session(conn, sid)
    try:
        await bot.edit_message_text(
            chat_id=session["chat_id"],
            message_id=session["message_id"],
            text=card_text(session, db.list_signups(conn, sid)),
            reply_markup=card_keyboard(session),
        )
    except RetryAfter:
        raise
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.warning("Could not update session card %s: %s", sid, exc)
    except TelegramError as exc:
        log.warning("Could not update session card %s: %s", sid, exc)


async def _refresh_now(bot, conn: sqlite3.Connection, sid: int) -> bool:
    """Refresh immediately, honoring Telegram's retry delay a few times."""
    for attempt in range(3):
        try:
            await refresh_card(bot, conn, sid)
            return True
        except RetryAfter as exc:
            if attempt == 2:
                log.warning("Rate-limited updating session card %s after retries", sid)
                return False
            delay = exc.retry_after
            if hasattr(delay, "total_seconds"):
                delay = delay.total_seconds()
            await asyncio.sleep(max(0.0, min(float(delay), 60.0)))
    return False


async def _job_refresh(context) -> None:
    sid = context.job.data
    try:
        await refresh_card(context.bot, context.bot_data["db"], sid)
    except RetryAfter as exc:
        context.job_queue.run_once(
            _job_refresh, when=exc.retry_after, data=sid, name=f"session-card-{sid}"
        )


async def request_card_refresh(context, sid: int) -> None:
    """Edit the card soon: at most one edit per CARD_EDIT_DELAY, showing the
    latest list (Telegram allows ~20 group messages a minute)."""
    jq = context.application.job_queue
    if jq is None:
        await _refresh_now(context.bot, _db(context), sid)
        return
    name = f"session-card-{sid}"
    if not jq.get_jobs_by_name(name):
        jq.run_once(_job_refresh, when=CARD_EDIT_DELAY, data=sid, name=name)


# --- Handlers -----------------------------------------------------------------


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _db(context)
    if not _is_admin(conn, update.effective_user.id):
        await update.message.reply_text(NOT_ADMIN)
        return
    if not db.get_setting(conn, db.GROUP_KEYS["rec"]):
        await update.message.reply_text(NO_REC_GROUP)
        return
    parts = update.message.text.split(None, 1)
    now = _now()
    if len(parts) == 1:
        last = db.last_posted_session(conn)
        template = _template(last, now)
        buttons = None
        if last is not None:
            summary = template.split("\n")[1:]
            buttons = InlineKeyboardMarkup(
                [[InlineKeyboardButton(
                    "Same as last time: " + " · ".join(summary) + " spots",
                    callback_data="ss:again",
                )]]
            )
        await update.message.reply_text(
            "Copy this (tap it), change what you need, and send it back:\n\n"
            f"<pre>/session\n{html.escape(template)}</pre>\n\n{html.escape(USAGE)}",
            parse_mode="HTML",
            reply_markup=buttons,
        )
        return
    try:
        fields = parse_session(parts[1], now)
    except ValueError as exc:
        await update.message.reply_text(f"Not posted: {exc}.\n\n{USAGE}")
        return
    session = db.create_session(
        conn,
        **fields,
        host_id=update.effective_user.id,
        host_name=update.effective_user.full_name[:NAME_CHARS],
    )
    text, keyboard = _preview(session)
    await update.message.reply_text(text, reply_markup=keyboard)


async def _admin_tap(update: Update, context) -> sqlite3.Connection | None:
    query = update.callback_query
    conn = _db(context)
    if not _is_admin(conn, update.effective_user.id):
        await query.answer(NOT_ADMIN, show_alert=True)
        return None
    await query.answer()
    return conn


async def on_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = await _admin_tap(update, context)
    if conn is None:
        return
    fields = parse_session(_template(db.last_posted_session(conn), _now()), _now())
    session = db.create_session(
        conn,
        **fields,
        host_id=update.effective_user.id,
        host_name=update.effective_user.full_name[:NAME_CHARS],
    )
    text, keyboard = _preview(session)
    await update.callback_query.edit_message_text(text, reply_markup=keyboard)


async def on_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = await _admin_tap(update, context)
    if conn is None:
        return
    query = update.callback_query
    session = db.get_session(conn, int(query.data.split(":")[2]))
    if session is None or session["cancelled_at"] or session["message_id"]:
        await query.edit_message_text("This preview was already posted or cancelled.")
        return
    if _start(session) <= _now():
        await query.edit_message_text("Not posted: that session has already started.")
        return
    chat_id = db.get_setting(conn, db.GROUP_KEYS["rec"])
    if not chat_id:
        await query.edit_message_text(NO_REC_GROUP)
        return
    try:
        message = await context.bot.send_message(
            chat_id=int(chat_id),
            text=card_text(session, []),
            reply_markup=card_keyboard(session),
        )
    except TelegramError as exc:
        log.warning("Could not post session %s: %s", session["id"], exc)
        await query.edit_message_text(
            f"Couldn't post to the rec group ({exc}). Is the bot still in it?"
        )
        return
    db.set_session_posted(conn, session["id"], message.chat_id, message.message_id)
    await query.edit_message_text(
        "✅ Posted to the rec group.\n\n" + card_text(session, []),
        reply_markup=_posted_keyboard(session["id"]),
    )


async def on_discard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = await _admin_tap(update, context)
    if conn is None:
        return
    query = update.callback_query
    session = db.get_session(conn, int(query.data.split(":")[2]))
    if session and not session["message_id"]:
        db.cancel_session(conn, session["id"])
    await query.edit_message_text("Not posted.")


async def on_cancel_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = await _admin_tap(update, context)
    if conn is None:
        return
    query = update.callback_query
    sid = int(query.data.split(":")[2])
    session = db.get_session(conn, sid)
    await query.edit_message_text(
        f"Cancel the {when_text(session)} session? Everyone in the group will see it.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Yes, cancel it", callback_data=f"ss:cc:{sid}"),
                InlineKeyboardButton("No, keep it", callback_data=f"ss:k:{sid}"),
            ]]
        ),
    )


async def on_cancel_keep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = await _admin_tap(update, context)
    if conn is None:
        return
    query = update.callback_query
    sid = int(query.data.split(":")[2])
    session = db.get_session(conn, sid)
    await query.edit_message_text(
        "✅ Posted to the rec group.\n\n" + card_text(session, db.list_signups(conn, sid)),
        reply_markup=_posted_keyboard(sid),
    )


async def on_cancel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = await _admin_tap(update, context)
    if conn is None:
        return
    query = update.callback_query
    sid = int(query.data.split(":")[2])
    if db.get_session(conn, sid)["cancelled_at"]:
        await query.edit_message_text("Already cancelled.")
        return
    db.cancel_session(conn, sid)
    session = db.get_session(conn, sid)
    await _refresh_now(context.bot, conn, sid)
    await _group_post(
        context.bot, session, f"❌ The {html.escape(when_text(session))} session is cancelled."
    )
    await query.edit_message_text(f"Cancelled the {when_text(session)} session.")


def _closed_reason(session) -> str | None:
    if session is None or session["message_id"] is None:
        return "This session no longer exists."
    if session["cancelled_at"]:
        return "This session was cancelled."
    if _now() >= _start(session):
        return "This session has started; sign-ups are closed."
    return None


async def on_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user, conn = update.callback_query, update.effective_user, _db(context)
    sid = int(query.data.split(":")[2])
    session = db.get_session(conn, sid)
    if reason := _closed_reason(session):
        await query.answer(reason, show_alert=True)
        return
    added = db.add_signup(conn, sid, user.id, user.full_name[:NAME_CHARS])
    ids = [s["user_id"] for s in db.list_signups(conn, sid)]
    place, cap = ids.index(user.id) + 1, session["capacity"]
    if place <= cap:
        text = f"You're in (#{place} of {cap})." if added else f"You're already in (#{place})."
    else:
        text = f"Session full: you're #{place - cap} on the waitlist."
        text += " You'll move up automatically if someone leaves." if added else ""
    await query.answer(text)
    if added:
        await request_card_refresh(context, sid)


async def on_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Leave from the card, or "I can't make it" in the primary's DM."""
    query, user, conn = update.callback_query, update.effective_user, _db(context)
    sid = int(query.data.split(":")[2])
    session = db.get_session(conn, sid)
    if reason := _closed_reason(session):
        await query.answer(reason, show_alert=True)
        return
    names = _names(conn, session)
    before = _player_ids(conn, session)
    if not db.remove_signup(conn, sid, user.id):
        await query.answer("You weren't signed up.")
        return
    promoted = [p for p in _player_ids(conn, session) if p not in before]
    # Hand over the role before any await, so the noon pick job can't interleave.
    roles = (session["primary_id"], session["secondary_id"])
    had_role = session["picked_at"] is not None and user.id in roles
    if had_role:
        keep = [p for p in roles if p and p != user.id]
        db.set_session_pick(conn, sid, *_fill_roles(conn, session, keep))
    await query.answer("You've left this session.")
    if query.message is not None and query.message.chat.type == "private":
        await query.edit_message_reply_markup(reply_markup=None)
    for person in promoted:
        await _dm(
            context.bot,
            person,
            f"🏸 A spot opened up: you're in for {session['title']}, "
            f"{when_text(session)} at {session['venue']}.",
        )
    await request_card_refresh(context, sid)
    if had_role:
        await _announce_handoff(context.bot, conn, sid, user.id, names, roles)


async def _announce_handoff(bot, conn, sid, leaver, old_names, old_roles) -> None:
    session = db.get_session(conn, sid)
    if session["announced_at"] is None:
        return  # the pending noon announcement will name the new pair
    names = _names(conn, session) | {leaver: old_names.get(leaver, "Someone")}
    primary, secondary = session["primary_id"], session["secondary_id"]
    when = html.escape(when_text(session))
    if primary is None:
        text = f"🧺 {when}: nobody is left to collect the nets and shuttles."
    else:
        text = (
            f"🧺 Change for {when}: {html.escape(names[leaver])} can't make it. "
            f"{mention_html(primary, names[primary])} is now collecting the nets and "
            "shuttles from the store room."
        )
        if secondary:
            text += f"\nBackup: {mention_html(secondary, names[secondary])}."
    if not await _group_post(bot, session, text):
        # The stored roles are authoritative; make the hourly job publish them.
        db.mark_session_unannounced(conn, sid)
        return
    await _dm_roles(
        bot,
        session,
        names,
        primary_changed=primary != old_roles[0],
        secondary_changed=secondary != old_roles[1],
    )


def build_handlers(private) -> list:
    return [
        CommandHandler("session", cmd_session, filters=private),
        CallbackQueryHandler(on_again, pattern=r"^ss:again$"),
        CallbackQueryHandler(on_post, pattern=r"^ss:p:\d+$"),
        CallbackQueryHandler(on_discard, pattern=r"^ss:x:\d+$"),
        CallbackQueryHandler(on_cancel_ask, pattern=r"^ss:c:\d+$"),
        CallbackQueryHandler(on_cancel_keep, pattern=r"^ss:k:\d+$"),
        CallbackQueryHandler(on_cancel_confirm, pattern=r"^ss:cc:\d+$"),
        CallbackQueryHandler(on_join, pattern=r"^ss:j:\d+$"),
        CallbackQueryHandler(on_leave, pattern=r"^ss:l:\d+$"),
    ]
