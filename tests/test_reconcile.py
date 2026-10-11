import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from clubbot import bot, db, reconcile
from clubbot.payments import SCHOOL_BILL_NUMBER, SCHOOL_UEN, SINGAPORE_TIME, BankLine
from clubbot.payments import reconcile as match


def payment(pid, name, at, *, amount=2500, txn=None, refs=(), confirmed=None):
    return {
        "id": pid,
        "full_name": name,
        "amount_cents": amount,
        "payment_timestamp": at,
        "bank_txn_id": txn,
        "extracted_json": json.dumps({"transaction_id": txn, "reference_numbers": list(refs)}),
        "audit_confirmed_at": confirmed,
    }


def line(at, name, *, amount=2500, txn=None):
    return BankLine(amount_cents=amount, paid_at=at, payer_name=name, transaction_id=txn)


# FLYMAX screenshots covering 10:00-18:00 on 11 Oct.
EDGES = [line("2026-10-11T10:00:00+08:00", "EARLY BIRD", amount=1), line("2026-10-11T18:00:00+08:00", "LATE OWL", amount=1)]


def test_transaction_id_match_confirms_even_with_a_different_name():
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00", txn="2610112184655201", refs=("261011-2184655201",))
    result = match([alice], [line("2026-10-11T12:03:00+08:00", "MDM LIM", txn="2610112184655201"), *EDGES])
    assert result.confirmed == (1,)
    assert result.missing == () and result.name_differs == ()


def test_fake_inside_the_covered_period_is_missing_and_outside_waits():
    fake = payment(1, "Faker Ng", "2026-10-11T12:00:00+08:00", txn="9999")
    later = payment(2, "Later Koh", "2026-10-11T20:00:00+08:00", txn="8888")
    result = match([fake, later], EDGES)
    assert result.missing == (1,)
    assert result.waiting == 1
    assert len(result.stray_lines) == 2


def test_name_and_time_match_within_the_window_only():
    alice = payment(1, "Alice Tan Wei Ling", "2026-10-11T12:00:00+08:00")
    assert match([alice], [line("2026-10-11T12:14:00+08:00", "TAN WEI LING"), *EDGES]).confirmed == (1,)
    far = match([alice], [line("2026-10-11T12:20:00+08:00", "TAN WEI LING"), *EDGES])
    assert far.confirmed == () and far.missing == (1,)


def test_amount_must_match():
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00", amount=3000)
    assert match([alice], [line("2026-10-11T12:00:00+08:00", "ALICE TAN"), *EDGES]).missing == (1,)


def test_a_fake_cannot_borrow_an_already_confirmed_members_line():
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00", confirmed="2026-10-11T13:00:00+00:00")
    fake = payment(2, "Bob Tan", "2026-10-11T12:00:00+08:00")
    result = match([fake, alice], [line("2026-10-11T12:00:00+08:00", "ALICE TAN"), *EDGES])
    assert result.confirmed == ()  # Alice was already confirmed
    assert result.ambiguous == (2,)


def test_a_line_that_fits_two_payments_confirms_neither():
    # Greedy matching would hand the line to whichever came first, maybe a fake.
    honest = payment(1, "Alice Tan", "2026-10-11T12:05:00+08:00")
    fake = payment(2, "Ben Tan", "2026-10-11T12:00:00+08:00")
    result = match([fake, honest], [line("2026-10-11T12:05:00+08:00", "ALICE TAN"), *EDGES])
    assert result.confirmed == ()
    assert sorted(result.ambiguous) == [1, 2]


def test_school_billing_id_read_as_a_bank_transaction_id_matches_nobody():
    refs = (SCHOOL_UEN, SCHOOL_BILL_NUMBER, "9999")
    fake = payment(1, "Faker Ng", "2026-10-11T12:00:00+08:00", txn="9999", refs=refs)
    result = match([fake], [line("2026-10-11T15:00:00+08:00", "SOMEONE", txn=SCHOOL_BILL_NUMBER), *EDGES])
    assert result.confirmed == () and result.missing == (1,)


def test_a_transaction_id_shared_by_two_receipts_matches_neither_by_id():
    a = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00", refs=("SHARED1",))
    b = payment(2, "Faker Ng", "2026-10-11T12:00:00+08:00", refs=("SHARED1",))
    result = match([a, b], [line("2026-10-11T12:00:00+08:00", "ALICE TAN", txn="SHARED1"), *EDGES])
    assert result.confirmed == (1,)  # by name + time, not the shared ID
    assert 2 not in result.confirmed


def test_one_transfer_read_with_and_without_its_id_is_one_line():
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00")
    copy = payment(2, "Alice Tan", "2026-10-11T12:00:00+08:00")
    rows = [line("2026-10-11T12:00:00+08:00", "ALICE TAN", txn="T1"), line("2026-10-11T12:00:30+08:00", "ALICE TAN")]
    result = match([alice, copy], [*rows, *EDGES])
    assert result.confirmed == ()
    assert len(result.stray_lines) == 3  # T1 (unclaimed) + the two edges


def test_only_the_name_differs_goes_to_the_treasurer():
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00")
    result = match([alice], [line("2026-10-11T12:01:00+08:00", "MR LIM BOON KENG"), *EDGES])
    assert [pid for pid, _ in result.name_differs] == [1]
    assert result.confirmed == () and result.missing == ()


def test_overlapping_screenshots_dont_double_count_a_line():
    row = line("2026-10-11T12:00:00+08:00", "ALICE TAN")
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00")
    twin = payment(2, "Alice Tan", "2026-10-11T12:00:00+08:00")  # e.g. a forged copy
    result = match([alice, twin], [row, row, *EDGES])
    assert result.confirmed == ()  # one transfer, two claims: the treasurer decides
    assert sorted(result.ambiguous) == [1, 2]


def test_date_only_lines_match_the_same_day_and_never_flag_edge_days():
    alice = payment(1, "Alice Tan", "2026-10-11T23:50:00+08:00")
    lines = [line("2026-10-10", "X", amount=1), line("2026-10-11", "ALICE TAN"), line("2026-10-13", "Y", amount=1)]
    assert match([alice], lines).confirmed == (1,)
    edge = payment(2, "Bob Lee", "2026-10-13T09:00:00+08:00")
    middle = payment(3, "Cat Ong", "2026-10-12T09:00:00+08:00")
    result = match([edge, middle], lines)
    assert result.missing == (3,) and result.waiting == 1


def test_unreadable_rows_are_skipped():
    result = match([], [line(None, "X"), line("2026-10-11T12:00:00+08:00", "Y", amount=None)])
    assert result.unreadable_lines == 2 and result.covered is None


# --- the Telegram flow --------------------------------------------------------


def _setup(conn):
    db.ensure_treasurer(conn, 999)
    today = datetime.now(SINGAPORE_TIME).date().isoformat()
    term = db.create_term(conn, name="T", fee_cents=2500, start_date=today, end_date="2099-12-31", created_by=999)
    now = datetime.now(SINGAPORE_TIME).replace(microsecond=0)
    for uid, name, sutd, txn in ((111, "Alice Tan", "1010001", "REAL1"), (222, "Faker Ng", "1010002", "FAKE1")):
        db.add_member(conn, telegram_user_id=uid, full_name=name, sutd_id=sutd, username=None)
        pid = db.mark_qr_issued(conn, db.get_or_create_payment(conn, member_id=uid, term_id=term["id"])["id"])["id"]
        db.save_verification_result(
            conn, pid, status="verified", amount_cents=2500, extracted_json="{}",
            bank_txn_id=txn, payment_timestamp=now.isoformat(), verified_by="auto",
        )
    return now


def _context(conn, lines):
    context = MagicMock()
    extractor = MagicMock()
    extractor.extract_bank_lines = AsyncMock(return_value=lines)
    context.bot_data = {"db": conn, "extractor": extractor}
    context.user_data = {}
    context.application.job_queue = None
    context.application.bot_data = context.bot_data
    context.bot.send_message = AsyncMock()
    telegram_file = MagicMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"flymax"))
    context.bot.get_file = AsyncMock(return_value=telegram_file)
    return context


def _update(user_id=999, photo=False, data=None):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    update.message.photo = [MagicMock(file_id="F", file_size=10)] if photo else []
    update.message.document = None
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


def test_reconcile_flow_confirms_real_and_flags_fake():
    conn = db.connect(":memory:")
    now = _setup(conn)
    lines = [
        line(now.isoformat(), "ALICE TAN", txn="REAL1"),
        line((now - timedelta(hours=3)).isoformat(), "SOMEONE", amount=1),
        line((now + timedelta(hours=3)).isoformat(), "SOMEONE ELSE", amount=1),
    ]
    context = _context(conn, lines)

    asyncio.run(reconcile.cmd_reconcile(_update(), context))
    assert reconcile.collecting(context)
    photo = _update(photo=True)
    asyncio.run(bot.on_receipt(photo, context))  # routed to the FLYMAX reader, not receipts
    assert "Read 3 payment(s)" in photo.message.reply_text.call_args.args[0]

    asyncio.run(reconcile.on_button(_update(data="rc:done"), context))
    assert not reconcile.collecting(context)
    report = context.bot.send_message.call_args.kwargs
    assert "Faker Ng" in report["text"] and "⚠️" in report["text"]
    assert report["reply_markup"] is not None
    counts = db.bank_counts(conn, db.get_active_term(conn)["id"])
    assert counts == {"confirmed": 1, "flagged": 1, "waiting": 0}

    fake_id = db.get_current_payment(conn, 222)["id"]
    asyncio.run(reconcile.on_button(_update(data=f"rc:ok:{fake_id}"), context))
    assert db.bank_counts(conn, db.get_active_term(conn)["id"])["confirmed"] == 2


def test_only_the_treasurer_can_reconcile():
    conn = db.connect(":memory:")
    _setup(conn)
    update = _update(user_id=111)
    context = _context(conn, [])
    asyncio.run(reconcile.cmd_reconcile(update, context))
    assert not reconcile.collecting(context)
    assert "Only the treasurer" in update.message.reply_text.call_args.args[0]
