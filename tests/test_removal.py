import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest

from clubbot import bot, db, removal

REC, COMP = -1002, -1001
TREASURER = 999
START, DEADLINE, END = date(2026, 9, 1), date(2026, 9, 15), date(2026, 12, 1)
REMOVAL_DAY = date(2026, 9, 22)  # deadline + 7 default grace days


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(removal, "KICK_PAUSE", 0)


@pytest.fixture()
def conn():
    conn = db.connect(":memory:")
    db.ensure_treasurer(conn, TREASURER)
    db.set_setting(conn, "rec_group_id", str(REC))
    db.set_setting(conn, "comp_group_id", str(COMP))
    return conn


def _term(conn, start=START, deadline=DEADLINE, end=END):
    return db.create_term(
        conn,
        name="Term 1",
        fee_cents=2000,
        rec_fee_cents=2500,
        recshirt_fee_cents=3000,
        shirt_fee_cents=1500,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        deadline=deadline.isoformat(),
        created_by=TREASURER,
    )


def _member(conn, uid, sutd_id, name):
    db.add_member(conn, telegram_user_id=uid, full_name=name, sutd_id=sutd_id, username=None)


def _seen(conn, uid, name, day):
    db.note_rec_person(conn, uid, name)
    stamp = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).isoformat()
    conn.execute(
        "UPDATE rec_group_people SET first_seen_at = ? WHERE telegram_user_id = ?",
        (stamp, uid),
    )
    conn.commit()


def _pay(conn, uid, term, status):
    payment = db.get_or_create_payment(conn, member_id=uid, term_id=term["id"])
    conn.execute("UPDATE payments SET status = ? WHERE id = ?", (status, payment["id"]))
    conn.commit()


def make_bot(statuses):
    """statuses: user_id -> ChatMemberStatus in the rec chat (absent = never there)."""
    fake = AsyncMock()

    async def get_chat_member(chat_id, user_id):
        if user_id not in statuses:
            raise BadRequest("User not found")
        return MagicMock(status=statuses[user_id], user=MagicMock(is_bot=False))

    fake.get_chat_member = AsyncMock(side_effect=get_chat_member)
    fake.get_chat_member_count = AsyncMock(return_value=len(statuses) + 3)
    return fake


def _dm_text(fake, chat_id):
    return [
        c.kwargs["text"] for c in fake.send_message.call_args_list
        if c.kwargs["chat_id"] == chat_id
    ]


def _kicked(fake):
    return sorted(c.args[1] for c in fake.ban_chat_member.call_args_list)


# --- the rule ----------------------------------------------------------------------


def test_due_date_present_at_start_vs_late_joiner(conn):
    term = _term(conn)
    assert removal.due_date(conn, term, None) == REMOVAL_DAY
    _seen(conn, 1, "Early", date(2026, 8, 20))
    assert removal.due_date(conn, term, db.get_rec_person(conn, 1)["first_seen_at"]) == REMOVAL_DAY
    _seen(conn, 2, "Late", date(2026, 10, 1))
    assert removal.due_date(conn, term, db.get_rec_person(conn, 2)["first_seen_at"]) == date(2026, 10, 8)
    db.set_setting(conn, "removal_grace_days", "3")
    assert removal.removal_day(conn, term) == date(2026, 9, 18)


def test_rejoining_does_not_reset_first_seen(conn):
    _seen(conn, 1, "Alice", date(2026, 9, 2))
    first = db.get_rec_person(conn, 1)["first_seen_at"]
    db.note_rec_person(conn, 1, "Alice T")
    person = db.get_rec_person(conn, 1)
    assert person["first_seen_at"] == first and person["name"] == "Alice T"


def test_who_is_spared(conn):
    term = _term(conn)
    for uid, status in (
        (1, "verified"),
        (2, "pending_verification"),
        (3, "exception"),
        (4, "rejected"),
        (5, "awaiting_payment"),
    ):
        _member(conn, uid, f"101000{uid}", f"M{uid}")
        _pay(conn, uid, term, status)
    _member(conn, 6, "1010006", "Kept")
    db.set_rec_keep(conn, 6, "Kept", True)
    _member(conn, 7, "1010007", "Admin")
    db.add_admin(conn, telegram_user_id=7, added_by=TREASURER)
    _seen(conn, 8, "Lurker", date(2026, 8, 1))  # never registered
    overdue = {
        p["telegram_user_id"]
        for p in db.list_rec_candidates(conn)
        if removal.is_overdue(conn, term, p, REMOVAL_DAY)
    }
    assert overdue == {4, 5, 8}
    assert not any(
        removal.is_overdue(conn, term, p, REMOVAL_DAY - timedelta(days=1))
        for p in db.list_rec_candidates(conn)
    )


def test_competitive_payment_keeps_you_in_the_rec_chat(conn):
    term = _term(conn)
    db.replace_roster(conn, [("Comp Player", "1010001")])
    _member(conn, 1, "1010001", "Comp Player")
    _pay(conn, 1, term, "verified")
    person = db.list_rec_candidates(conn)[0]
    assert not removal.is_overdue(conn, term, person, REMOVAL_DAY)


# --- scheduling: preview, warning, sweep ----------------------------------------------


def _run(fake, conn, term, day):
    asyncio.run(removal.run_due(fake, conn, term, day))
    # Stamps hold the real clock; move them to the simulated day.
    conn.execute(
        "UPDATE term_events SET sent_at = ? WHERE sent_at > ?",
        (datetime(day.year, day.month, day.day, 3, tzinfo=timezone.utc).isoformat(),
         datetime(day.year, day.month, day.day, 23, tzinfo=timezone.utc).isoformat()),
    )
    conn.commit()


def _setup_people(conn, term):
    _member(conn, 1, "1010001", "Unpaid Alice")
    _member(conn, 2, "1010002", "Paid Bob")
    _pay(conn, 2, term, "verified")
    _seen(conn, 3, "Lurker Carl", date(2026, 8, 1))
    _member(conn, 4, "1010004", "Not In Chat Dan")
    _seen(conn, 5, "Group Admin Eve", date(2026, 8, 1))
    return make_bot(
        {
            1: ChatMemberStatus.MEMBER,
            2: ChatMemberStatus.MEMBER,
            3: ChatMemberStatus.MEMBER,
            4: ChatMemberStatus.LEFT,
            5: ChatMemberStatus.ADMINISTRATOR,
        }
    )


def test_preview_mode_lists_people_but_removes_nobody(conn):
    term = _term(conn)
    fake = _setup_people(conn, term)
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=3))
    fake.send_message.assert_not_awaited()  # too early
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))
    (preview,) = _dm_text(fake, TREASURER)
    assert "Unpaid Alice" in preview and "Lurker Carl" in preview
    assert "Paid Bob" not in preview and "Eve" not in preview and "Dan" not in preview
    assert "PREVIEW MODE" in preview
    assert "can identify 4" in preview
    for day in range(0, 10):
        _run(fake, conn, term, REMOVAL_DAY + timedelta(days=day))
    fake.ban_chat_member.assert_not_awaited()
    assert _dm_text(fake, 1) == []  # no warning in preview mode
    assert len(_dm_text(fake, TREASURER)) == 1  # preview sent once


def test_on_mode_warns_then_removes_on_removal_day(conn):
    term = _term(conn)
    fake = _setup_people(conn, term)
    db.set_setting(conn, "remove_unpaid", "on")
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))
    assert "will remove" in _dm_text(fake, TREASURER)[0]
    assert "removed from the rec group chat on 2026-09-22" in _dm_text(fake, 1)[0]
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=1))
    fake.ban_chat_member.assert_not_awaited()

    _run(fake, conn, term, REMOVAL_DAY)
    assert _kicked(fake) == [1, 3]
    for call in fake.unban_chat_member.call_args_list:  # kick, not a ban
        assert call.kwargs["only_if_banned"] is True
    assert "You've been removed" in _dm_text(fake, 1)[-1]
    summary = _dm_text(fake, TREASURER)[-1]
    assert "Removed 2" in summary and "Unpaid Alice" in summary

    _run(fake, conn, term, REMOVAL_DAY)  # same day again: stamped, no repeat
    assert fake.ban_chat_member.await_count == 2


def test_large_preview_is_chunked_and_every_candidate_has_keep_button(conn):
    term = _term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    statuses = {}
    for uid in range(1, 184):
        _seen(conn, uid, f"Member {uid:03d} With A Long Display Name", date(2026, 8, 1))
        statuses[uid] = ChatMemberStatus.MEMBER
    fake = make_bot(statuses)

    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))

    treasurer_calls = [
        call for call in fake.send_message.call_args_list
        if call.kwargs["chat_id"] == TREASURER
    ]
    assert len(treasurer_calls) > 1
    assert all(len(call.kwargs["text"]) <= 4096 for call in treasurer_calls)
    keep_ids = {
        int(button.callback_data.split(":")[1])
        for call in treasurer_calls
        if (markup := call.kwargs.get("reply_markup"))
        for row in markup.inline_keyboard
        for button in row
    }
    assert keep_ids == set(statuses)


def test_transient_warning_failure_retries_before_preview_is_complete(conn):
    term = _term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    _member(conn, 1, "1010001", "Unpaid Alice")
    fake = make_bot({1: ChatMemberStatus.MEMBER})
    attempts = {1: 0}

    async def send_message(*args, **kwargs):
        if kwargs["chat_id"] == 1:
            attempts[1] += 1
            if attempts[1] == 1:
                raise BadRequest("temporary warning failure")
        return MagicMock()

    fake.send_message = AsyncMock(side_effect=send_message)
    preview_day = REMOVAL_DAY - timedelta(days=2)
    _run(fake, conn, term, preview_day)
    _run(fake, conn, term, preview_day)

    assert attempts[1] == 2
    assert db.term_event_claimed(conn, term["id"], "removal-previewed:1")


def test_unban_failure_is_persisted_and_retried_hourly(conn):
    term = _term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    _member(conn, 1, "1010001", "Unpaid Alice")
    fake = make_bot({1: ChatMemberStatus.MEMBER})
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))
    fake.unban_chat_member = AsyncMock(
        side_effect=[BadRequest("temporary unban failure"), None]
    )

    _run(fake, conn, term, REMOVAL_DAY)
    assert [row["telegram_user_id"] for row in db.list_pending_unbans(conn, REC)] == [1]
    _run(fake, conn, term, REMOVAL_DAY)

    assert fake.unban_chat_member.await_count == 2
    assert db.list_pending_unbans(conn, REC) == []


def test_crash_immediately_after_ban_still_leaves_unban_recovery(conn):
    class SimulatedCrash(BaseException):
        pass

    term = _term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    _member(conn, 1, "1010001", "Unpaid Alice")
    fake = make_bot({1: ChatMemberStatus.MEMBER})
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))
    fake.ban_chat_member = AsyncMock(side_effect=SimulatedCrash)

    with pytest.raises(SimulatedCrash):
        _run(fake, conn, term, REMOVAL_DAY)

    assert [row["telegram_user_id"] for row in db.list_pending_unbans(conn, REC)] == [1]


def test_paying_during_the_buffer_saves_you(conn):
    term = _term(conn)
    fake = _setup_people(conn, term)
    db.set_setting(conn, "remove_unpaid", "on")
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))
    _pay(conn, 1, term, "pending_verification")
    _run(fake, conn, term, REMOVAL_DAY)
    assert _kicked(fake) == [3]


def test_late_switch_on_still_gives_two_days_warning(conn):
    term = _term(conn)
    fake = _setup_people(conn, term)
    db.set_setting(conn, "remove_unpaid", "on")
    late = REMOVAL_DAY + timedelta(days=5)
    _run(fake, conn, term, late)  # preview + warnings only
    fake.ban_chat_member.assert_not_awaited()
    _run(fake, conn, term, late + timedelta(days=1))
    fake.ban_chat_member.assert_not_awaited()
    _run(fake, conn, term, late + timedelta(days=2))
    assert _kicked(fake) == [1, 3]


def test_late_joiner_removed_after_own_grace(conn):
    term = _term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    _seen(conn, 9, "Freshman", date(2026, 10, 1))
    fake = make_bot({9: ChatMemberStatus.MEMBER})
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))
    for offset in range(0, 20):
        _run(fake, conn, term, REMOVAL_DAY + timedelta(days=offset))
        if fake.ban_chat_member.await_count:
            break
    assert REMOVAL_DAY + timedelta(days=offset) == date(2026, 10, 8)


def test_registered_late_joiner_gets_own_preview_and_final_warning(conn):
    term = _term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    _member(conn, 9, "1010009", "Freshman")
    _seen(conn, 9, "Freshman", date(2026, 10, 1))
    fake = make_bot({9: ChatMemberStatus.MEMBER})

    _run(fake, conn, term, date(2026, 10, 6))

    assert any("Freshman" in text for text in _dm_text(fake, TREASURER))
    assert any("removed" in text for text in _dm_text(fake, 9))
    assert db.term_event_claimed(conn, term["id"], "removal-previewed:9")


def test_kick_failure_is_reported_and_retried_next_day(conn):
    term = _term(conn)
    fake = _setup_people(conn, term)
    db.set_setting(conn, "remove_unpaid", "on")
    _run(fake, conn, term, REMOVAL_DAY - timedelta(days=2))
    fake.ban_chat_member = AsyncMock(side_effect=BadRequest("Not enough rights"))
    _run(fake, conn, term, REMOVAL_DAY)
    assert "Could not remove 2" in _dm_text(fake, TREASURER)[-1]
    _run(fake, conn, term, REMOVAL_DAY)  # no hourly retry spam
    assert fake.ban_chat_member.await_count == 2
    _run(fake, conn, term, REMOVAL_DAY + timedelta(days=1))
    assert fake.ban_chat_member.await_count == 4


def test_nothing_happens_without_rec_chat_or_when_off(conn):
    term = _term(conn)
    fake = _setup_people(conn, term)
    db.set_setting(conn, "remove_unpaid", "off")
    _run(fake, conn, term, REMOVAL_DAY)
    db.set_setting(conn, "remove_unpaid", "on")
    db.delete_setting(conn, "rec_group_id")
    _run(fake, conn, term, REMOVAL_DAY)
    fake.send_message.assert_not_awaited()
    fake.ban_chat_member.assert_not_awaited()


def test_removal_notice_only_when_on(conn):
    term = _term(conn)
    assert removal.removal_notice(conn, term) == ""
    db.set_setting(conn, "remove_unpaid", "on")
    assert "2026-09-22" in removal.removal_notice(conn, term)


# --- Telegram handlers --------------------------------------------------------------


def _context(conn):
    context = MagicMock()
    context.bot_data = {"db": conn}
    context.bot.send_message = AsyncMock()
    return context


def _join_request(chat_id, user_id, name="New Person"):
    update = MagicMock()
    request = update.chat_join_request
    request.chat.id = chat_id
    request.from_user.id = user_id
    request.from_user.full_name = name
    request.user_chat_id = user_id
    request.approve = AsyncMock()
    request.decline = AsyncMock()
    return update, request


def _open_term(conn):
    today = date.today()
    return _term(
        conn,
        start=today - timedelta(days=30),
        deadline=today - timedelta(days=20),
        end=today + timedelta(days=30),
    )


def test_join_request_welcomes_then_approves_newcomer(conn):
    _open_term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    update, request = _join_request(REC, 50)
    context = _context(conn)
    order = []
    context.bot.send_message.side_effect = lambda **kw: order.append("dm")
    request.approve.side_effect = lambda: order.append("approve")
    asyncio.run(removal.on_join_request(update, context))
    assert order == ["dm", "approve"]  # the DM window closes once handled
    assert "Welcome" in context.bot.send_message.call_args.kwargs["text"]
    assert db.get_rec_person(conn, 50) is not None


def test_join_request_declines_removed_unpaid_until_they_pay(conn):
    term = _open_term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    _member(conn, 51, "1010051", "Removed Ray")
    _seen(conn, 51, "Removed Ray", date.today() - timedelta(days=40))
    update, request = _join_request(REC, 51)
    context = _context(conn)
    asyncio.run(removal.on_join_request(update, context))
    request.decline.assert_awaited_once()
    request.approve.assert_not_awaited()
    assert "isn't paid" in context.bot.send_message.call_args.kwargs["text"]

    _pay(conn, 51, term, "verified")
    update, request = _join_request(REC, 51)
    asyncio.run(removal.on_join_request(update, context))
    request.approve.assert_awaited_once()


def test_comp_chat_is_never_touched(conn):
    _open_term(conn)
    db.set_setting(conn, "remove_unpaid", "on")
    update, request = _join_request(COMP, 60)
    context = _context(conn)
    asyncio.run(removal.on_join_request(update, context))
    request.approve.assert_not_awaited()
    request.decline.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()

    change = MagicMock()
    change.chat_member.chat.id = COMP
    change.chat_member.new_chat_member.status = ChatMemberStatus.MEMBER
    change.chat_member.new_chat_member.user.id = 61
    change.chat_member.new_chat_member.user.is_bot = False
    asyncio.run(removal.on_chat_member(change, context))
    assert db.get_rec_person(conn, 60) is None and db.get_rec_person(conn, 61) is None

    change.chat_member.chat.id = REC
    change.chat_member.new_chat_member.user.full_name = "Rec Joiner"
    asyncio.run(removal.on_chat_member(change, context))
    assert db.get_rec_person(conn, 61)["name"] == "Rec Joiner"


def test_keep_button_and_unkeep(conn):
    _member(conn, 1, "1010001", "Coach Kim")
    query_update = MagicMock()
    query_update.effective_user.id = TREASURER
    query = query_update.callback_query
    query.data = "kp:1"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message.reply_markup.inline_keyboard = [
        [MagicMock(callback_data="kp:1")],
        [MagicMock(callback_data="kp:2")],
    ]
    asyncio.run(removal.on_keep_toggle(query_update, _context(conn)))
    assert [k["name"] for k in db.list_rec_kept(conn)] == ["Coach Kim"]
    markup = query.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    assert len(markup.inline_keyboard) == 1  # the tapped button is gone

    query.data = "uk:1"
    asyncio.run(removal.on_keep_toggle(query_update, _context(conn)))
    assert db.list_rec_kept(conn) == []

    query_update.effective_user.id = 1  # not the treasurer
    query.data = "kp:1"
    asyncio.run(removal.on_keep_toggle(query_update, _context(conn)))
    assert db.list_rec_kept(conn) == []


def test_someone_seen_in_rec_chat_may_register_while_outside_it(conn):
    context = _context(conn)
    context.bot.get_chat_member = AsyncMock(
        return_value=MagicMock(status=ChatMemberStatus.LEFT)
    )
    assert not asyncio.run(bot._in_club_group(context, 70))
    db.note_rec_person(conn, 70, "Removed Lurker")
    assert asyncio.run(bot._in_club_group(context, 70))
