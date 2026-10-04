import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from telegram.error import Forbidden

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


# --- Pure due-date calculators ------------------------------------------------

TERM = {"start_date": "2026-09-01", "deadline": "2026-09-15"}  # starts on a Tuesday


def _events(term, calc, days=20):
    start = date.fromisoformat(term["start_date"])
    days = (start + timedelta(days=n) for n in range(days))
    return {day.isoformat(): calc(term, day) for day in days if calc(term, day)}


def test_reminder_events_days_3_7_10_13_then_last_call():
    assert _events(TERM, scheduler.reminder_event) == {
        "2026-09-04": "remind-d3",
        "2026-09-08": "remind-d7",
        "2026-09-11": "remind-d10",
        "2026-09-14": "remind-d13",
        "2026-09-15": "lastcall",
    }


def test_reminder_events_only_before_short_deadline():
    term = {"start_date": "2026-09-01", "deadline": "2026-09-08"}  # deadline = day 7
    assert _events(term, scheduler.reminder_event) == {
        "2026-09-04": "remind-d3",
        "2026-09-08": "lastcall",
    }


def test_group_posts_mondays_and_thursdays_until_deadline():
    assert sorted(_events(TERM, scheduler.group_post_event)) == [
        "2026-09-03", "2026-09-07", "2026-09-10", "2026-09-14",
    ]
    assert scheduler.group_post_event(TERM, date(2026, 9, 7)) == "grouppost-2026-09-07"


# --- run_due_events ------------------------------------------------------------


def _sgt(day, hour, minute=0):
    return datetime.fromisoformat(day).replace(
        hour=hour, minute=minute, tzinfo=SINGAPORE_TIME
    )


def _fixed_term(conn, *, blasted=True):
    term = db.create_term(
        conn, name="Term 1", fee_cents=2000, start_date="2026-09-01",
        end_date="2026-12-01", deadline="2026-09-15", created_by=999,
    )
    if blasted:
        db.mark_term_start_notified(conn, term["id"])
    return term


def _sent_to(fake_bot):
    return [call.kwargs["chat_id"] for call in fake_bot.send_message.await_args_list]


def test_due_reminder_skips_paid_and_opted_out_and_never_resends(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    _member(conn, 333, "1000003", "Cara")
    term = _fixed_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    db.opt_out(conn, db.get_or_create_payment(conn, member_id=333, term_id=term["id"])["id"])

    fake_bot = make_bot()
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-04", 10, 1)))
    assert _sent_to(fake_bot) == [222]
    kwargs = fake_bot.send_message.await_args.kwargs
    assert "2026-09-15" in kwargs["text"]
    buttons = [row[0].callback_data for row in kwargs["reply_markup"].inline_keyboard]
    assert buttons == ["pay:start", f"optout:{term['id']}"]

    # A restart re-runs the check: the stamp stops a second send.
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-04", 15)))
    assert _sent_to(fake_bot) == [222]


def test_due_reminder_waits_for_ten_am_and_skips_non_reminder_days(conn):
    _member(conn, 111, "1000001", "Alice")
    _fixed_term(conn)
    fake_bot = make_bot()
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-04", 9, 59)))
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-05", 10, 1)))
    fake_bot.send_message.assert_not_awaited()


def test_due_last_call_on_deadline_and_nothing_after(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _fixed_term(conn, blasted=False)
    fake_bot = make_bot()
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-16", 10, 1)))
    fake_bot.send_message.assert_not_awaited()  # not even the unsent blast

    db.mark_term_start_notified(conn, term["id"])
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-15", 10, 1)))
    assert "Last call" in fake_bot.send_message.await_args.kwargs["text"]


def test_due_same_day_blast_replaces_reminder(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _fixed_term(conn, blasted=False)  # e.g. /newterm run on day 3
    fake_bot = make_bot()
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-04", 10, 1)))
    assert _sent_to(fake_bot) == [111]
    assert "collection is open" in fake_bot.send_message.await_args.kwargs["text"]
    assert db.get_term(conn, term["id"])["start_notified_at"] is not None
    assert db.claim_term_event(conn, term["id"], "remind-d3") is False


def test_group_posts_counts_only_no_names_once(conn):
    db.replace_roster(conn, [("Alice Tan", "1000001"), ("Ghost Lee", "1010999")])
    _member(conn, 111, "1000001", "Alice Tan")
    _member(conn, 222, "1000002", "Bob Lim")
    _member(conn, 333, "1000003", "Cara Ng")
    term = _fixed_term(conn)
    for uid in (111, 222):
        db.mark_paid_manual(conn, member_id=uid, term_id=term["id"])
    db.set_setting(conn, "comp_group_id", "-1001")
    db.set_setting(conn, "rec_group_id", "-1002")

    fake_bot = make_bot()
    fake_bot.username = "ShuttleBot"
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-07", 11, 1)))
    fake_bot.send_message.assert_not_awaited()  # Monday, but before 12:00
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-07", 12, 1)))
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-07", 13, 1)))

    posts = {c.kwargs["chat_id"]: c.kwargs["text"] for c in fake_bot.send_message.await_args_list}
    assert fake_bot.send_message.await_count == 2
    assert "Competitive: 1/2 paid" in posts[-1001]
    assert "1 rec members paid so far" in posts[-1002]
    for text in posts.values():
        assert "https://t.me/ShuttleBot?start=pay" in text
        assert "2026-09-15" in text
        for name in ("Alice", "Bob", "Cara", "Ghost"):
            assert name not in text


def test_group_posts_switch_off_and_send_failure(conn):
    _fixed_term(conn)
    db.set_setting(conn, "rec_group_id", "-1002")
    db.set_setting(conn, "comp_group_id", "-1001")
    fake_bot = make_bot()
    db.set_setting(conn, "group_posts", "off")
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-03", 12, 1)))
    fake_bot.send_message.assert_not_awaited()

    db.set_setting(conn, "group_posts", "on")
    fake_bot.send_message.side_effect = Exception("bot was kicked")
    asyncio.run(scheduler.run_due_events(fake_bot, conn, _sgt("2026-09-03", 12, 1)))
    assert fake_bot.send_message.await_count == 2  # both tried, no crash


# --- do_term_start_blast -------------------------------------------------------


def test_term_start_blast_messages_unverified_only(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    _member(conn, 333, "1000003", "Cara")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=333, term_id=term["id"])  # already verified

    fake_bot = make_bot()
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))

    sent_to = {call.kwargs["chat_id"] for call in fake_bot.send_message.await_args_list}
    assert sent_to == {111, 222}
    assert fake_bot.send_message.await_count == 2
    kwargs = fake_bot.send_message.await_args_list[0].kwargs
    assert "Term 5" in kwargs["text"]
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert (button.text, button.callback_data) == ("Pay now", "pay:start")
    fake_bot.send_photo.assert_not_awaited()  # amount depends on shirt choice
    assert db.get_term(conn, term["id"])["start_notified_at"] is not None
    # No QR went out, so the payment-time window must not have started.
    assert db.get_current_payment(conn, 111)["qr_issued_at"] is None


def test_term_start_blast_skips_members_who_already_got_a_qr(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.mark_qr_issued(conn, payment["id"])  # e.g. sent before a mid-blast reboot

    fake_bot = make_bot()
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))

    sent_to = {call.kwargs["chat_id"] for call in fake_bot.send_message.await_args_list}
    assert sent_to == {222}


def test_term_start_blast_resumes_after_mid_blast_restart(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    fake_bot = make_bot()
    class Crash(BaseException):  # not caught by the per-member `except Exception`
        pass

    fake_bot.send_message.side_effect = [None, Crash()]  # process dies after Alice
    with pytest.raises(Crash):
        asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))
    assert db.get_term(conn, term["id"])["start_notified_at"] is None

    fake_bot = make_bot()
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))
    sent_to = [call.kwargs["chat_id"] for call in fake_bot.send_message.await_args_list]
    assert sent_to == [222]


def test_term_start_blast_total_failure_leaves_term_unstamped(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _term(conn)

    fake_bot = make_bot()
    fake_bot.send_message.side_effect = Exception("telegram down")
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))

    assert db.get_term(conn, term["id"])["start_notified_at"] is None
    # And the member is not falsely marked as having received a QR.
    assert db.get_current_payment(conn, 111)["qr_issued_at"] is None


def test_term_start_blast_continues_after_send_failure(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)

    fake_bot = make_bot()
    fake_bot.send_message.side_effect = [Forbidden("blocked"), None]
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))

    assert fake_bot.send_message.await_count == 2
    assert db.get_term(conn, term["id"])["start_notified_at"] is not None


def test_term_start_blast_retries_only_transient_failures(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    fake_bot = make_bot()
    fake_bot.send_message.side_effect = [Exception("timeout"), None]
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))
    assert db.get_term(conn, term["id"])["start_notified_at"] is None

    fake_bot = make_bot()
    asyncio.run(scheduler.do_term_start_blast(fake_bot, conn, term["id"]))
    assert [c.kwargs["chat_id"] for c in fake_bot.send_message.call_args_list] == [111]
    assert db.get_term(conn, term["id"])["start_notified_at"] is not None


def test_reminder_retries_only_members_whose_send_failed(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    fake_bot = make_bot()
    fake_bot.send_message.side_effect = [Exception("timeout"), None]
    asyncio.run(scheduler.send_unpaid_reminders(fake_bot, conn, term["id"], event="remind-d3"))
    fake_bot = make_bot()
    asyncio.run(scheduler.send_unpaid_reminders(fake_bot, conn, term["id"], event="remind-d3"))
    assert [c.kwargs["chat_id"] for c in fake_bot.send_message.call_args_list] == [111]


def test_backup_due_weekly_and_after_downtime(conn):
    now = datetime(2026, 10, 4, 3, 0, tzinfo=SINGAPORE_TIME)
    assert scheduler.backup_due(conn, now)  # never backed up
    db.set_setting(conn, "last_backup_at", (now - timedelta(days=6)).isoformat())
    assert not scheduler.backup_due(conn, now)
    db.set_setting(conn, "last_backup_at", (now - timedelta(days=9)).isoformat())
    assert scheduler.backup_due(conn, now)  # bot was down on the usual day


# --- send_unpaid_reminders ----------------------------------------------------


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
    markup = fake_bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "pay:start"


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


# --- do_weekly_backup ----------------------------------------------------------


def _capture_document(sent):
    async def send_document(*, chat_id, document, filename, caption):
        sent.update(
            chat_id=chat_id, path=document.name, data=document.read(), filename=filename
        )

    return send_document


def test_weekly_backup_sends_db_copy_and_deletes_temp_file(conn):
    _member(conn, 111, "1000001", "Alice")
    db.ensure_treasurer(conn, 999)
    sent = {}
    fake_bot = make_bot()
    fake_bot.send_document = AsyncMock(side_effect=_capture_document(sent))

    assert asyncio.run(scheduler.do_weekly_backup(fake_bot, conn)) is True

    assert sent["chat_id"] == 999
    assert sent["filename"].startswith("clubbot-") and sent["filename"].endswith(".db")
    assert sent["data"].startswith(b"SQLite format 3\x00")
    assert not os.path.exists(sent["path"])


def test_weekly_backup_swallows_send_failure(conn):
    db.ensure_treasurer(conn, 999)
    sent = {}
    capture = _capture_document(sent)

    async def failing_send(**kwargs):
        await capture(**kwargs)
        raise RuntimeError("Telegram down")

    fake_bot = make_bot()
    fake_bot.send_document = AsyncMock(side_effect=failing_send)

    assert asyncio.run(scheduler.do_weekly_backup(fake_bot, conn)) is False
    assert not os.path.exists(sent["path"])


def test_weekly_backup_without_treasurer(conn):
    fake_bot = make_bot()
    assert asyncio.run(scheduler.do_weekly_backup(fake_bot, conn)) is False
    fake_bot.send_document.assert_not_awaited()


# --- schedule_all smoke --------------------------------------------------------


def test_schedule_all_smoke(conn):
    _member(conn, 111, "1000001", "Alice")
    _term(conn)
    app = bot.build_application("1234567:TESTTOKEN", conn)
    jobs = {job.name for job in app.job_queue.jobs()}
    assert {"weekly-backup", "due-check", "due-check-now", "sheet-nightly", "extract-retry"} <= jobs


def test_schedule_term_jobs_runs_a_due_check(conn):
    # A term created while the bot is running must be checked right away,
    # not only at the next hourly check.
    app = bot.build_application("1234567:TESTTOKEN", conn)
    before = len(app.job_queue.get_jobs_by_name("due-check-now"))
    term = _term(conn)
    scheduler.schedule_term_jobs(app, conn, term["id"])
    assert len(app.job_queue.get_jobs_by_name("due-check-now")) == before + 1
