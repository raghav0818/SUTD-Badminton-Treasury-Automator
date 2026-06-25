import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from clubbot import bot, db, scheduler

SINGAPORE_TIME = timezone(timedelta(hours=8))


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def make_bot():
    fake = AsyncMock()
    fake.send_photo = AsyncMock()
    fake.send_message = AsyncMock()
    return fake


def _term(conn, *, fee_cents=2000, start_offset=-1, end_offset=30):
    today = date.today()
    return db.create_term(
        conn,
        name="Term 5",
        fee_cents=fee_cents,
        start_date=(today + timedelta(days=start_offset)).isoformat(),
        end_date=(today + timedelta(days=end_offset)).isoformat(),
        created_by=999,
    )


def _member(conn, uid, sutd_id, name="Member"):
    db.add_member(
        conn, telegram_user_id=uid, full_name=name, sutd_id=sutd_id, username=None
    )


# --- Pure run-time calculators -------------------------------------------------


def test_term_start_run_time_future_start_is_ten_am_sgt():
    now = datetime(2026, 6, 18, 12, 0, tzinfo=SINGAPORE_TIME)
    term = {"start_date": "2026-06-20"}
    when = scheduler.term_start_run_time(term, now)
    assert when == datetime(2026, 6, 20, 10, 0, tzinfo=SINGAPORE_TIME)


def test_term_start_run_time_past_start_returns_now():
    now = datetime(2026, 6, 25, 15, 30, tzinfo=SINGAPORE_TIME)
    term = {"start_date": "2026-06-20"}
    assert scheduler.term_start_run_time(term, now) == now


def test_reminder7_run_time_is_seven_days_after_start():
    term = {"start_date": "2026-06-20"}
    assert scheduler.reminder7_run_time(term) == datetime(
        2026, 6, 27, 10, 0, tzinfo=SINGAPORE_TIME
    )


# --- pending_term_jobs ---------------------------------------------------------


def test_pending_term_jobs_fresh_term_yields_both(conn):
    term = _term(conn)
    now = datetime.now(SINGAPORE_TIME)
    jobs = scheduler.pending_term_jobs(conn, now)
    kinds = [kind for kind, _, _ in jobs]
    assert kinds == ["start", "reminder7"]
    assert all(t["id"] == term["id"] for _, t, _ in jobs)


def test_pending_term_jobs_drops_start_after_stamp(conn):
    term = _term(conn)
    db.mark_term_start_notified(conn, term["id"])
    jobs = scheduler.pending_term_jobs(conn, datetime.now(SINGAPORE_TIME))
    assert [kind for kind, _, _ in jobs] == ["reminder7"]


def test_pending_term_jobs_ignores_ended_term(conn):
    today = date.today()
    db.create_term(
        conn,
        name="Old Term",
        fee_cents=2000,
        start_date=(today - timedelta(days=40)).isoformat(),
        end_date=(today - timedelta(days=10)).isoformat(),
        created_by=999,
    )
    jobs = scheduler.pending_term_jobs(conn, datetime.now(SINGAPORE_TIME))
    assert jobs == []


# --- do_term_start_blast -------------------------------------------------------


def test_term_start_blast_messages_unverified_only(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    _member(conn, 333, "1000003", "Cara")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=333, term_id=term["id"])  # already verified

    fake_bot = make_bot()
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))

    sent_to = {call.kwargs["chat_id"] for call in fake_bot.send_photo.await_args_list}
    assert sent_to == {111, 222}
    assert fake_bot.send_photo.await_count == 2
    caption = fake_bot.send_photo.await_args_list[0].kwargs["caption"]
    assert "S$20.00" in caption
    assert db.get_term(conn, term["id"])["start_notified_at"] is not None


def test_term_start_blast_continues_after_send_failure(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)

    fake_bot = make_bot()
    fake_bot.send_photo.side_effect = [Exception("blocked"), None]
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))

    assert fake_bot.send_photo.await_count == 2
    assert db.get_term(conn, term["id"])["start_notified_at"] is not None


# --- send_unpaid_reminders / do_reminder7 --------------------------------------


def test_send_unpaid_reminders_counts_and_skips_verified(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])

    fake_bot = make_bot()
    count = asyncio.run(scheduler.send_unpaid_reminders(fake_bot, conn, term["id"]))

    assert count == 1
    sent_to = {call.kwargs["chat_id"] for call in fake_bot.send_message.await_args_list}
    assert sent_to == {222}


def test_send_unpaid_reminders_counts_only_successful(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    _term(conn)

    fake_bot = make_bot()
    fake_bot.send_message.side_effect = [Exception("blocked"), None]
    count = asyncio.run(
        scheduler.send_unpaid_reminders(fake_bot, conn, db.list_terms(conn)[0]["id"])
    )

    assert count == 1


def test_do_reminder7_stamps_term(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _term(conn)
    fake_bot = make_bot()
    asyncio.run(scheduler.do_reminder7(fake_bot, conn, term["id"]))
    assert db.get_term(conn, term["id"])["reminder7_sent_at"] is not None


# --- do_audit_digest -----------------------------------------------------------


def test_audit_digest_returns_false_when_nothing(conn):
    db.ensure_treasurer(conn, 999)
    fake_bot = make_bot()
    assert asyncio.run(scheduler.do_audit_digest(fake_bot, conn)) is False
    fake_bot.send_message.assert_not_awaited()


def test_audit_digest_returns_false_without_treasurer(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    fake_bot = make_bot()
    assert asyncio.run(scheduler.do_audit_digest(fake_bot, conn)) is False
    fake_bot.send_message.assert_not_awaited()


def test_audit_digest_dms_treasurer_with_button(conn):
    _member(conn, 111, "1000001", "Alice")
    db.ensure_treasurer(conn, 999)
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])

    fake_bot = make_bot()
    assert asyncio.run(scheduler.do_audit_digest(fake_bot, conn)) is True

    kwargs = fake_bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 999
    assert "Alice" in kwargs["text"]
    assert "S$20.00" in kwargs["text"]
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == "audit:allfound"


# --- schedule_all smoke --------------------------------------------------------


def test_schedule_all_smoke(conn):
    _member(conn, 111, "1000001", "Alice")
    _term(conn)
    app = bot.build_application("1234567:TESTTOKEN", conn)
    scheduler.schedule_all(app, conn)
    jobs = {job.name for job in app.job_queue.jobs()}
    assert "audit-digest" in jobs
    assert len(app.job_queue.jobs()) >= 1


def test_schedule_term_jobs_arms_a_live_term(conn):
    # A term created while the bot is already running must get its jobs armed
    # immediately, not only after a restart.
    app = bot.build_application("1234567:TESTTOKEN", conn)
    term = _term(conn)
    scheduler.schedule_term_jobs(app, conn, term["id"])
    names = {job.name for job in app.job_queue.jobs()}
    assert f"start-{term['id']}" in names
    assert f"reminder7-{term['id']}" in names


def test_schedule_term_jobs_is_idempotent(conn):
    app = bot.build_application("1234567:TESTTOKEN", conn)
    term = _term(conn)
    scheduler.schedule_term_jobs(app, conn, term["id"])
    scheduler.schedule_term_jobs(app, conn, term["id"])
    start_jobs = [j for j in app.job_queue.jobs() if j.name == f"start-{term['id']}"]
    assert len(start_jobs) == 1
