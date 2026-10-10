import asyncio
import random
from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, RetryAfter

from clubbot import db, sessions
from clubbot.db import SINGAPORE_TIME

HOST = 500
REC_GROUP = -100123
# Wednesday 2026-10-14, 09:00 SGT; the session below is Thu 15 Oct 7pm-11pm.
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=SINGAPORE_TIME)
SESSION_TEXT = "/session\nBadminton Recre🏸\nThu 15 Oct\n7pm-11pm\nISH 2\n3"


@pytest.fixture()
def conn():
    conn = db.connect(":memory:")
    conn.execute("INSERT OR IGNORE INTO admins (telegram_user_id, role) VALUES (?, 'admin')", (HOST,))
    conn.commit()
    db.set_setting(conn, db.GROUP_KEYS["rec"], str(REC_GROUP))
    return conn


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now = {"t": NOW}
    monkeypatch.setattr(sessions, "_now", lambda: now["t"])
    return now


def make_context(conn):
    context = MagicMock()
    context.bot_data = {"db": conn}
    context.application.job_queue = None
    context.bot.send_message = AsyncMock(
        return_value=MagicMock(chat_id=REC_GROUP, message_id=77)
    )
    context.bot.edit_message_text = AsyncMock()
    return context


def _user(update, user_id, name):
    update.effective_user.id = user_id
    update.effective_user.full_name = name


def make_command(text, user_id=HOST):
    update = MagicMock()
    _user(update, user_id, "Mike Chen")
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def make_tap(data, user_id, name="Someone"):
    update = MagicMock()
    _user(update, user_id, name)
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


def tap(handler, data, context, user_id, name="Someone"):
    update = make_tap(data, user_id, name)
    asyncio.run(handler(update, context))
    return update


def answer_of(update) -> str:
    call = update.callback_query.answer.call_args
    return call.args[0] if call.args else call.kwargs.get("text", "")


def posted_session(conn, context):
    """Run /session + [Post]; return the session id."""
    update = make_command(SESSION_TEXT)
    asyncio.run(sessions.cmd_session(update, context))
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    post = markup.inline_keyboard[0][0].callback_data
    tap(sessions.on_post, post, context, HOST)
    return int(post.split(":")[2])


def join(context, sid, *people):
    for user_id in people:
        tap(sessions.on_join, f"ss:j:{sid}", context, user_id, f"P{user_id}")


def sent_texts(context) -> list[str]:
    return [c.kwargs["text"] for c in context.bot.send_message.call_args_list]


# --- parsing ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, start, end",
    [
        ("7pm-11pm", time(19), time(23)),
        ("7-11pm", time(19), time(23)),
        ("7:30pm - 10pm", time(19, 30), time(22)),
        ("19:00-23:00", time(19), time(23)),
        ("10am-1pm", time(10), time(13)),
        ("7pm–11", time(19), time(23)),
    ],
)
def test_parse_times(text, start, end):
    assert sessions.parse_times(text) == (start, end)


@pytest.mark.parametrize("text", ["7pm", "11pm-7pm", "13pm-2pm", "evening"])
def test_parse_times_rejects(text):
    with pytest.raises(ValueError):
        sessions.parse_times(text)


def test_parse_day_forms():
    today = date(2026, 10, 14)
    assert sessions.parse_day("16 Oct", today) == date(2026, 10, 16)
    assert sessions.parse_day("Thu 15 Oct", today) == date(2026, 10, 15)
    assert sessions.parse_day("16/10", today) == date(2026, 10, 16)
    assert sessions.parse_day("2026-10-16", today) == date(2026, 10, 16)
    # A day-month already past this year means next year.
    assert sessions.parse_day("5 Jan", today) == date(2027, 1, 5)


def test_parse_session_rejects_bad_input():
    with pytest.raises(ValueError, match="5 lines"):
        sessions.parse_session("Title\n15 Oct\n7pm-11pm\nISH 2", NOW)
    with pytest.raises(ValueError, match="spots"):
        sessions.parse_session("T\n15 Oct\n7pm-11pm\nISH 2\n0", NOW)
    with pytest.raises(ValueError, match="already started"):
        sessions.parse_session("T\n14 Oct\n8am-9am\nISH 2\n20", NOW)


# --- the pick -----------------------------------------------------------------


def test_choose_prefers_least_picked_and_never_repeats():
    counts = {1: 3, 2: 0, 3: 0, 4: 1}
    for seed in range(20):
        chosen = sessions.choose([1, 2, 3, 4], counts, 2, random.Random(seed))
        assert sorted(chosen) == [2, 3]
    assert sessions.choose([7], {}, 2) == [7]
    assert sessions.choose([], {}, 2) == []


def test_pick_time_is_noon_or_noon_the_day_before_for_early_sessions():
    evening = {"starts_at": "2026-10-15T19:00:00+08:00"}
    morning = {"starts_at": "2026-10-15T10:00:00+08:00"}
    assert sessions.pick_time(evening) == datetime(2026, 10, 15, 12, tzinfo=SINGAPORE_TIME)
    assert sessions.pick_time(morning) == datetime(2026, 10, 14, 12, tzinfo=SINGAPORE_TIME)


# --- posting and the card ---------------------------------------------------------


def test_non_admin_cannot_post(conn):
    update = make_command(SESSION_TEXT, user_id=123)
    asyncio.run(sessions.cmd_session(update, make_context(conn)))
    assert "admins" in update.message.reply_text.call_args.args[0]


def test_session_alone_offers_template_and_same_as_last_time(conn):
    context = make_context(conn)
    update = make_command("/session")
    asyncio.run(sessions.cmd_session(update, context))
    assert update.message.reply_text.call_args.kwargs["reply_markup"] is None  # no last session
    posted_session(conn, context)
    update = make_command("/session")
    asyncio.run(sessions.cmd_session(update, context))
    button = update.message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Same as last time: Thu 15 Oct · 7pm-11pm · ISH 2 · 3 spots"
    again = tap(sessions.on_again, "ss:again", context, HOST)
    assert "Thu 15 Oct · 7pm–11pm" in again.callback_query.edit_message_text.call_args.args[0]


def test_post_sends_card_to_rec_group_once(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    call = context.bot.send_message.call_args.kwargs
    assert call["chat_id"] == REC_GROUP
    assert "👥 Participants · 0 of 3" in call["text"]
    assert db.get_session(conn, sid)["message_id"] == 77
    again = tap(sessions.on_post, f"ss:p:{sid}", context, HOST)
    assert "already posted" in again.callback_query.edit_message_text.call_args.args[0]
    assert context.bot.send_message.call_count == 1


def test_join_full_waitlist_and_promotion_on_leave(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    join(context, sid, 1, 2, 3)
    late = tap(sessions.on_join, f"ss:j:{sid}", context, 4, "P4")
    assert "#1 on the waitlist" in answer_of(late)
    card = context.bot.edit_message_text.call_args.kwargs["text"]
    assert "👥 Participants · 3 of 3" in card and "⏳ Waitlist · 1" in card
    again = tap(sessions.on_join, f"ss:j:{sid}", context, 1, "P1")
    assert "already in (#1)" in answer_of(again)

    tap(sessions.on_leave, f"ss:l:{sid}", context, 2)
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 4  # promotion DM
    card = context.bot.edit_message_text.call_args.kwargs["text"]
    assert "Waitlist" not in card and "3. P4" in card


def test_taps_refused_after_start_and_after_cancel(conn, clock):
    context = make_context(conn)
    sid = posted_session(conn, context)
    clock["t"] = datetime(2026, 10, 15, 19, 0, tzinfo=SINGAPORE_TIME)
    late = tap(sessions.on_join, f"ss:j:{sid}", context, 1)
    assert "started" in answer_of(late)
    clock["t"] = NOW
    tap(sessions.on_cancel_confirm, f"ss:cc:{sid}", context, HOST)
    assert "cancelled" in sent_texts(context)[-1]
    assert context.bot.edit_message_text.call_args.kwargs["reply_markup"] is None
    gone = tap(sessions.on_join, f"ss:j:{sid}", context, 1)
    assert "cancelled" in answer_of(gone)


def test_refresh_ignores_message_not_modified(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    context.bot.edit_message_text.side_effect = BadRequest("Message is not modified")
    join(context, sid, 1)  # must not raise


def test_immediate_refresh_retries_after_telegram_rate_limit(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    context.bot.edit_message_text.reset_mock()
    context.bot.edit_message_text.side_effect = [RetryAfter(0), None]

    asyncio.run(sessions._refresh_now(context.bot, conn, sid))

    assert context.bot.edit_message_text.await_count == 2


# --- the noon pick and handoff -------------------------------------------------------


def test_noon_pick_announces_once_from_players_only(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    join(context, sid, 1, 2, 3, 4)  # 4 is waitlisted
    bot = context.bot

    asyncio.run(sessions.run_due_picks(bot, conn, datetime(2026, 10, 15, 11, 30, tzinfo=SINGAPORE_TIME)))
    assert db.get_session(conn, sid)["picked_at"] is None

    noon = datetime(2026, 10, 15, 12, 0, 30, tzinfo=SINGAPORE_TIME)
    asyncio.run(sessions.run_due_picks(bot, conn, noon))
    session = db.get_session(conn, sid)
    primary, secondary = session["primary_id"], session["secondary_id"]
    assert {primary, secondary} < {1, 2, 3} and primary != secondary
    announcement = bot.send_message.call_args_list[-3].kwargs
    assert announcement["chat_id"] == REC_GROUP and announcement["parse_mode"] == "HTML"
    assert f"tg://user?id={primary}" in announcement["text"]
    assert f"tg://user?id={secondary}" in announcement["text"]
    dms = [c.kwargs["chat_id"] for c in bot.send_message.call_args_list[-2:]]
    assert dms == [primary, secondary]
    assert "🧺 Nets & shuttles" in bot.edit_message_text.call_args.kwargs["text"]

    calls = bot.send_message.call_count
    asyncio.run(sessions.run_due_picks(bot, conn, noon))  # e.g. after a restart
    assert bot.send_message.call_count == calls
    assert db.get_session(conn, sid)["primary_id"] == primary


def test_failed_announcement_keeps_pick_and_retries(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    join(context, sid, 1, 2)
    bot = context.bot
    bot.send_message.side_effect = BadRequest("chat not found")
    noon = datetime(2026, 10, 15, 12, 0, tzinfo=SINGAPORE_TIME)
    asyncio.run(sessions.run_due_picks(bot, conn, noon))
    session = db.get_session(conn, sid)
    assert session["picked_at"] is not None and session["announced_at"] is None
    bot.send_message.side_effect = None
    asyncio.run(sessions.run_due_picks(bot, conn, noon.replace(hour=13)))
    after = db.get_session(conn, sid)
    assert after["announced_at"] is not None
    assert (after["primary_id"], after["secondary_id"]) == (session["primary_id"], session["secondary_id"])


def test_zero_player_pick_notifies_host_once(conn):
    context = make_context(conn)
    posted_session(conn, context)
    context.bot.send_message.reset_mock()
    noon = datetime(2026, 10, 15, 12, tzinfo=SINGAPORE_TIME)

    asyncio.run(sessions.run_due_picks(context.bot, conn, noon))
    asyncio.run(sessions.run_due_picks(context.bot, conn, noon.replace(hour=13)))

    host_dms = [
        call.kwargs["text"] for call in context.bot.send_message.call_args_list
        if call.kwargs["chat_id"] == HOST
    ]
    assert len(host_dms) == 1
    assert "nobody" in host_dms[0].lower()


def test_zero_player_host_notification_retries_after_send_failure(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    context.bot.send_message.reset_mock()
    context.bot.send_message.side_effect = [BadRequest("temporary failure"), None]
    noon = datetime(2026, 10, 15, 12, tzinfo=SINGAPORE_TIME)

    asyncio.run(sessions.run_due_picks(context.bot, conn, noon))
    assert db.get_session(conn, sid)["empty_notified_at"] is None
    asyncio.run(sessions.run_due_picks(context.bot, conn, noon.replace(hour=13)))

    assert context.bot.send_message.await_count == 2
    assert db.get_session(conn, sid)["empty_notified_at"] is not None


def test_failed_handoff_group_post_is_retried_by_due_job(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    join(context, sid, 1, 2, 3)
    noon = datetime(2026, 10, 15, 12, tzinfo=SINGAPORE_TIME)
    asyncio.run(sessions.run_due_picks(context.bot, conn, noon))
    old = db.get_session(conn, sid)

    async def fail_group(*args, **kwargs):
        if kwargs.get("chat_id") == REC_GROUP:
            raise BadRequest("temporary group failure")
        return MagicMock()

    context.bot.send_message.side_effect = fail_group
    asyncio.run(sessions.on_leave(make_tap(f"ss:l:{sid}", old["primary_id"]), context))
    assert db.get_session(conn, sid)["announced_at"] is None

    context.bot.send_message.side_effect = None
    asyncio.run(sessions.run_due_picks(context.bot, conn, noon.replace(hour=13)))
    assert db.get_session(conn, sid)["announced_at"] is not None


def test_failed_nobody_left_handoff_is_retried_by_due_job(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    join(context, sid, 1)
    noon = datetime(2026, 10, 15, 12, tzinfo=SINGAPORE_TIME)
    asyncio.run(sessions.run_due_picks(context.bot, conn, noon))
    old = db.get_session(conn, sid)

    async def fail_group(*args, **kwargs):
        if kwargs.get("chat_id") == REC_GROUP:
            raise BadRequest("temporary group failure")
        return MagicMock()

    context.bot.send_message.side_effect = fail_group
    asyncio.run(sessions.on_leave(make_tap(f"ss:l:{sid}", old["primary_id"]), context))
    assert db.get_session(conn, sid)["announced_at"] is None

    context.bot.send_message.side_effect = None
    asyncio.run(sessions.run_due_picks(context.bot, conn, noon.replace(hour=13)))
    assert db.get_session(conn, sid)["announced_at"] is not None
    assert "nobody is left" in context.bot.send_message.call_args.kwargs["text"]


def test_primary_cant_make_it_hands_over_to_backup(conn):
    context = make_context(conn)
    sid = posted_session(conn, context)
    join(context, sid, 1, 2, 3)
    bot = context.bot
    asyncio.run(sessions.run_due_picks(bot, conn, datetime(2026, 10, 15, 12, tzinfo=SINGAPORE_TIME)))
    old = db.get_session(conn, sid)
    third = ({1, 2, 3} - {old["primary_id"], old["secondary_id"]}).pop()

    # The primary taps "I can't make it" in their DM.
    dm_tap = make_tap(f"ss:l:{sid}", old["primary_id"])
    dm_tap.callback_query.message.chat.type = "private"
    asyncio.run(sessions.on_leave(dm_tap, context))

    new = db.get_session(conn, sid)
    assert (new["primary_id"], new["secondary_id"]) == (old["secondary_id"], third)
    dm_tap.callback_query.edit_message_reply_markup.assert_awaited_once()
    group = [c.kwargs for c in bot.send_message.call_args_list if c.kwargs["chat_id"] == REC_GROUP]
    assert "can't make it" in group[-1]["text"]
    assert f"tg://user?id={old['secondary_id']}" in group[-1]["text"]
    # The old backup is told they're now collecting; the new backup gets the backup DM.
    dms = {c.kwargs["chat_id"]: c.kwargs["text"] for c in bot.send_message.call_args_list[-2:]}
    assert "You're collecting" in dms[old["secondary_id"]]
    assert "You're the backup" in dms[third]


def test_rotation_counts_past_primaries(conn):
    context = make_context(conn)
    first = posted_session(conn, context)
    db.set_session_pick(conn, first, 1, 2)
    db.mark_session_announced(conn, first)
    assert db.times_primary(conn, exclude_session_id=999) == {1: 1}
