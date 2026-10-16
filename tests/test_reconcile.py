import asyncio
import itertools
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


_TXN = itertools.count(1)


def line(at, name, *, amount=2500, txn="auto"):
    # Real FLYMAX rows always show a TXN ID; txn=None means Gemini missed it.
    txn = f"AUTO{next(_TXN)}" if txn == "auto" else txn
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
    # Nobody can tell which came first honestly, so BOTH go to the treasurer
    # and the earlier confirmation is suspended (it may be the fake).
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00", confirmed="2026-10-11T13:00:00+00:00")
    fake = payment(2, "Bob Tan", "2026-10-11T12:00:00+08:00")
    result = match([fake, alice], [line("2026-10-11T12:00:00+08:00", "ALICE TAN"), *EDGES])
    assert result.confirmed == ()
    assert sorted(result.ambiguous) == [1, 2]
    assert result.suspended == (1,)


def test_a_fake_that_claimed_a_transfer_first_is_suspended_when_the_real_payer_shows_up():
    # Finding 4: fake confirmed by T1 in an earlier run; the genuine receipt
    # arrives later. Both flagged, the fake loses its bank confirmation.
    fake = payment(1, "Ben Tan", "2026-10-11T12:00:00+08:00", confirmed="2026-10-11T13:00:00+00:00")
    real = payment(2, "Alice Tan", "2026-10-11T12:02:00+08:00")
    t1 = line("2026-10-11T12:01:00+08:00", "ALICE TAN", txn="T1")
    result = match([fake, real], [t1, *EDGES], owners={"T1": 1})
    assert result.confirmed == ()
    assert sorted(result.ambiguous) == [1, 2] and result.suspended == (1,)


def test_a_claimed_transfer_rebacks_only_its_owner_and_is_never_stray():
    alice = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00")
    t1 = line("2026-10-11T12:01:00+08:00", "ALICE TAN", txn="T1")
    result = match([alice], [t1, *EDGES], owners={"T1": 1})
    assert result.confirmed == (1,) and result.claimed_lines == ((1, "T1"),)
    # Owner revoked or from another term (not in this run): the transfer is
    # still theirs, so a look-alike receipt cannot take it.
    lookalike = payment(2, "Alice Lee", "2026-10-11T12:00:00+08:00")
    result = match([lookalike], [t1, *EDGES], owners={"T1": 99})
    assert result.confirmed == () and result.ambiguous == (2,)
    assert len(result.stray_lines) == 2  # only the edges


def test_an_old_transfers_id_pasted_into_a_new_receipt_does_not_match():
    # Finding 2: an October receipt listing a September transfer's ID.
    forged = payment(1, "Faker Ng", "2026-10-11T12:00:00+08:00", refs=("SEPT1",))
    old = line("2026-09-17T23:45:00+08:00", "RYAN GOH", txn="SEPT1")
    result = match([forged], [old, *EDGES])
    assert result.confirmed == () and result.missing == (1,)


def test_one_receipt_cannot_consume_two_transfers():
    # Finding 5: a receipt listing two real IDs is flagged, and the transfer it
    # wrongly claimed is still free for its real (no-ID) payer.
    greedy = payment(1, "Ben Koh", "2026-10-11T12:00:00+08:00", refs=("T1", "T2"))
    honest = payment(2, "Alice Tan", "2026-10-11T12:01:00+08:00")
    rows = [line("2026-10-11T12:00:00+08:00", "BEN KOH", txn="T1"), line("2026-10-11T12:01:00+08:00", "ALICE TAN", txn="T2")]
    result = match([greedy, honest], [*rows, *EDGES])
    assert result.confirmed == (2,)
    assert result.ambiguous == (1,)


def test_a_truncated_reread_of_one_transfer_cannot_confirm_a_second_receipt():
    # Finding 1: overlapping screenshots read "ALICE TAN" + ID once and just
    # "ALICE" (no ID) once. One transfer, so only one Alice can be confirmed.
    # Before the fix: Tan took REAL1 by ID, then the "ALICE" copy confirmed Lee.
    tan = payment(1, "Alice Tan", "2026-10-11T12:00:00+08:00", refs=("REAL1",))
    lee = payment(2, "Alice Lee", "2026-10-11T12:00:00+08:00")
    for rows in (
        [line("2026-10-11T12:00:00+08:00", "ALICE TAN", txn="REAL1"), line("2026-10-11T12:00:20+08:00", "ALICE", txn=None)],
        [line("2026-10-11T12:00:20+08:00", "ALICE", txn=None), line("2026-10-11T12:00:00+08:00", "ALICE TAN", txn="REAL1")],
    ):
        result = match([tan, lee], [*rows, *EDGES])
        assert result.confirmed == (1,)
        assert result.missing == (2,)


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
    rows = [line("2026-10-11T12:00:00+08:00", "ALICE TAN", txn="T1"), line("2026-10-11T12:00:30+08:00", "ALICE TAN", txn=None)]
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

    alice_id = db.get_current_payment(conn, 111)["id"]
    assert db.bank_line_owners(conn) == {"REAL1": alice_id}

    fake_id = db.get_current_payment(conn, 222)["id"]
    fine = _button(report["reply_markup"], f"rc:ok:{fake_id}:")
    asyncio.run(reconcile.on_button(_update(data=fine), context))
    assert db.bank_counts(conn, db.get_active_term(conn)["id"])["confirmed"] == 2


def test_a_new_receipt_after_revoke_does_not_inherit_the_bank_confirmation():
    # Finding 3: revoke a bank-confirmed payment, send different evidence: it
    # must be checked against the bank again, not show "confirmed" at once.
    conn = db.connect(":memory:")
    now = _setup(conn)
    term_id = db.get_active_term(conn)["id"]
    pid = db.get_current_payment(conn, 111)["id"]
    db.record_reconcile(conn, suspended=[], confirmed=[pid], claims=[(pid, "REAL1")], flagged=[])
    db.revoke_payment(conn, member_id=111, term_id=term_id)
    assert db.get_payment(conn, pid)["audit_confirmed_at"] is None

    db.mark_payment_pending(conn, pid, screenshot_file_id="F2", image_hash="h2")
    db.save_verification_result(
        conn, pid, status="verified", amount_cents=2500, extracted_json="{}",
        bank_txn_id="OTHER", payment_timestamp=now.isoformat(), verified_by="auto",
    )
    assert db.bank_label(db.get_payment(conn, pid)) == "waiting"
    assert db.bank_line_owners(conn) == {"REAL1": pid}  # the transfer stays theirs


def test_only_the_treasurer_can_reconcile():
    conn = db.connect(":memory:")
    _setup(conn)
    update = _update(user_id=111)
    context = _context(conn, [])
    asyncio.run(reconcile.cmd_reconcile(update, context))
    assert not reconcile.collecting(context)
    assert "Only the treasurer" in update.message.reply_text.call_args.args[0]


def test_every_transfer_a_payment_ever_used_stays_its_own():
    # Codex: Alice owns OLD; revoked, she resubmits and NEW confirms her; after
    # a second revoke, NEW must not be free for somebody else's fake receipt.
    conn = db.connect(":memory:")
    _setup(conn)
    pid = db.get_current_payment(conn, 111)["id"]
    db.record_reconcile(conn, suspended=[], confirmed=[pid], claims=[(pid, "OLD")], flagged=[])
    db.record_reconcile(conn, suspended=[], confirmed=[pid], claims=[(pid, "NEW")], flagged=[])
    assert db.bank_line_owners(conn) == {"OLD": pid, "NEW": pid}

    fake = payment(7, "Alice Lee", "2026-10-11T12:00:00+08:00")
    new_line = line("2026-10-11T12:00:00+08:00", "ALICE TAN", txn="NEW")
    result = match([fake], [new_line, *EDGES], owners=db.bank_line_owners(conn))
    assert result.confirmed == () and result.ambiguous == (7,)


def test_reconcile_saves_all_or_nothing():
    # Codex: a crash between "confirmed" and "transfer reserved" left a
    # confirmed payment whose transfer anyone could reuse later.
    conn = db.connect(":memory:")
    _setup(conn)
    pid = db.get_current_payment(conn, 111)["id"]
    conn.execute("INSERT INTO bank_line_claims VALUES ('T1', ?, 'x')", (pid,))
    conn.commit()
    conn.execute("DROP TABLE bank_line_claims")  # make the claim step blow up
    try:
        db.record_reconcile(conn, suspended=[], confirmed=[pid], claims=[(pid, "T2")], flagged=[])
    except Exception:
        pass
    assert db.get_payment(conn, pid)["audit_confirmed_at"] is None  # rolled back


def _button(markup, prefix):
    return next(
        b.callback_data for row in markup.inline_keyboard for b in row
        if b.callback_data.startswith(prefix)
    )


def test_old_its_fine_and_revoke_buttons_refuse_a_replacement_receipt():
    # Codex: revoke, member resubmits; the OLD report's buttons must not
    # confirm (It's fine) or revoke the NEW receipt.
    conn = db.connect(":memory:")
    now = _setup(conn)
    lines = [
        line(now.isoformat(), "ALICE TAN", txn="REAL1"),
        line((now - timedelta(hours=3)).isoformat(), "SOMEONE", amount=1),
        line((now + timedelta(hours=3)).isoformat(), "SOMEONE ELSE", amount=1),
    ]
    context = _context(conn, lines)
    asyncio.run(reconcile.cmd_reconcile(_update(), context))
    context.user_data[reconcile.LINES_KEY] = lines  # as if read from screenshots
    asyncio.run(reconcile.on_button(_update(data="rc:done"), context))
    markup = context.bot.send_message.call_args.kwargs["reply_markup"]
    fake_id = db.get_current_payment(conn, 222)["id"]
    old_fine = _button(markup, f"rc:ok:{fake_id}:")
    old_revoke = _button(markup, f"rc:rv:{fake_id}:")

    term_id = db.get_active_term(conn)["id"]
    db.revoke_payment(conn, member_id=222, term_id=term_id)
    db.mark_payment_pending(conn, fake_id, screenshot_file_id="F2", image_hash="h2")
    conn.execute("UPDATE payments SET status = 'verified', verified_at = '2099-01-01T00:00:00+00:00' WHERE id = ?", (fake_id,))
    conn.commit()

    asyncio.run(reconcile.on_button(_update(data=old_fine), context))
    assert db.get_payment(conn, fake_id)["audit_confirmed_at"] is None
    assert "older receipt" in context.bot.send_message.call_args.kwargs["text"]
    asyncio.run(reconcile.on_button(_update(data=old_revoke), context))
    assert db.get_payment(conn, fake_id)["status"] == "verified"


def test_a_row_read_without_its_txn_id_confirms_nobody():
    # Codex: A's transfer is claimed; A is revoked; a re-read missing the TXN
    # ID must not let B's look-alike receipt confirm through name + time.
    lookalike = payment(2, "Alice Lee", "2026-10-11T12:00:00+08:00")
    blind = line("2026-10-11T12:00:00+08:00", "ALICE TAN", txn=None)
    result = match([lookalike], [blind, *EDGES], owners={"T1": 99})
    assert result.confirmed == ()
    assert result.no_txn_lines == (blind,)


def test_a_receipt_matching_its_own_and_another_transfer_is_contested():
    # Codex: T1 already belongs to this payment; a replacement receipt
    # listing T1 AND T2 must be flagged, not re-confirmed via T1.
    greedy = payment(1, "Ben Koh", "2026-10-11T12:00:00+08:00", refs=("T1", "T2"))
    rows = [line("2026-10-11T12:00:00+08:00", "BEN KOH", txn="T1"), line("2026-10-11T12:01:00+08:00", "ALICE TAN", txn="T2")]
    result = match([greedy], [*rows, *EDGES], owners={"T1": 1})
    assert result.confirmed == () and result.ambiguous == (1,)
