import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from clubbot import bot, db
from clubbot.payments import SCHOOL_BILL_NUMBER, ExtractedPayment

SINGAPORE_TIME = timezone(timedelta(hours=8))


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def make_update(user_id=111, text=None, username="alice"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.message.text = text
    update.message.photo = []
    update.message.document = None
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    return update


def make_context(conn, extractor=None):
    context = MagicMock()
    context.bot_data = {"db": conn, "extractor": extractor}
    context.user_data = {}
    context.args = []
    context.bot.get_file = AsyncMock()
    context.bot.send_message = AsyncMock()
    # No real JobQueue in unit tests; /newterm's live scheduling is a no-op here
    # and is covered directly in test_scheduler.py.
    context.application.job_queue = None
    return context


def reply_text_of(update) -> str:
    return update.message.reply_text.call_args.args[0]


def test_start_unregistered_asks_for_name(conn):
    update, context = make_update(text="/start"), make_context(conn)
    assert asyncio.run(bot.cmd_start(update, context)) == bot.ASK_NAME
    assert "full name" in reply_text_of(update).lower()


def test_start_when_registered_shows_status(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    update, context = make_update(text="/start"), make_context(conn)
    assert asyncio.run(bot.cmd_start(update, context)) == ConversationHandler.END
    assert "Registered as Alice Tan" in reply_text_of(update)


def test_full_registration_flow(conn):
    context = make_context(conn)
    asyncio.run(bot.cmd_start(make_update(text="/start"), context))

    update = make_update(text="Alice Tan")
    assert asyncio.run(bot.on_name(update, context)) == bot.ASK_SUTD_ID

    update = make_update(text="1010765")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.CONFIRM
    assert "1010765" in reply_text_of(update)

    update = make_update(text="yes")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END

    member = db.get_member(conn, 111)
    assert member["full_name"] == "Alice Tan"
    assert member["sutd_id"] == "1010765"
    assert member["username"] == "alice"


def test_invalid_name_reprompts(conn):
    context = make_context(conn)
    update = make_update(text="12345")
    assert asyncio.run(bot.on_name(update, context)) == bot.ASK_NAME


def test_invalid_sutd_id_reprompts(conn):
    context = make_context(conn)
    context.user_data["full_name"] = "Alice Tan"
    update = make_update(text="not-an-id")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.ASK_SUTD_ID


def test_duplicate_sutd_id_blocked(conn):
    db.add_member(
        conn, telegram_user_id=999, full_name="Bob Lim", sutd_id="1010765", username=None
    )
    context = make_context(conn)
    context.user_data["full_name"] = "Alice Tan"
    update = make_update(text="1010765")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.ASK_SUTD_ID
    assert "already registered" in reply_text_of(update)


def test_confirm_no_cancels(conn):
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1007654"})
    update = make_update(text="no")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END
    assert db.get_member(conn, 111) is None


def test_confirm_gibberish_reprompts(conn):
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1007654"})
    update = make_update(text="maybe")
    assert asyncio.run(bot.on_confirm(update, context)) == bot.CONFIRM


def test_status_unregistered(conn):
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    assert "not registered" in reply_text_of(update)


def test_status_registered(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    assert "Alice Tan" in reply_text_of(update)


def test_status_shows_username_and_next_steps(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    text = reply_text_of(update)
    assert "@alice" in text
    assert "Fee collection is not open" in text


def test_status_refreshes_changed_username(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    update = make_update(text="/status", username="alice_new")
    asyncio.run(bot.cmd_status(update, make_context(conn)))
    assert db.get_member(conn, 111)["username"] == "alice_new"
    assert "@alice_new" in reply_text_of(update)


def test_start_refreshes_changed_username(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    update = make_update(text="/start", username="alice_new")
    asyncio.run(bot.cmd_start(update, make_context(conn)))
    assert db.get_member(conn, 111)["username"] == "alice_new"


def test_status_without_username_has_no_handle(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username=None
    )
    update = make_update(text="/status", username=None)
    asyncio.run(bot.cmd_status(update, make_context(conn)))
    text = reply_text_of(update)
    assert "Fee collection is not open" in text
    assert "@" not in text
    assert "(SUTD ID 1007654)" in text


def test_build_application_smoke(conn):
    app = bot.build_application("1234567:TESTTOKEN", conn)
    assert app.bot_data["db"] is conn
    assert len(app.handlers[0]) == 26


def create_active_term(conn, treasurer_id=999):
    today = date.today()
    return db.create_term(
        conn,
        name="Payment Test",
        fee_cents=5,
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=7)).isoformat(),
        created_by=treasurer_id,
    )


def test_treasurer_can_create_five_cent_term(conn):
    db.ensure_treasurer(conn, 999)
    update = make_update(user_id=999, text="/newterm")
    context = make_context(conn)
    today = date.today()
    context.args = [
        "Payment",
        "Test",
        "0.05",
        today.isoformat(),
        (today + timedelta(days=7)).isoformat(),
    ]
    asyncio.run(bot.cmd_newterm(update, context))
    assert db.get_active_term(conn)["fee_cents"] == 5
    assert "S$0.05" in reply_text_of(update)


def test_non_treasurer_cannot_create_term(conn):
    update = make_update(user_id=111, text="/newterm")
    context = make_context(conn)
    asyncio.run(bot.cmd_newterm(update, context))
    assert db.get_active_term(conn) is None
    assert "Only the treasurer" in reply_text_of(update)


def test_pay_sends_personal_qr(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    create_active_term(conn)
    update, context = make_update(text="/pay"), make_context(conn)
    asyncio.run(bot.cmd_pay(update, context))
    assert update.message.reply_photo.await_count == 1
    caption = update.message.reply_photo.call_args.kwargs["caption"]
    assert "S$0.05" in caption
    payment = db.get_current_payment(conn, 111)
    assert payment["qr_issued_at"] is not None
    assert "Billing ID" in caption


class FakeExtractor:
    def __init__(self, result):
        self.result = result

    async def extract(self, image_bytes, mime_type):
        assert image_bytes == b"receipt-image"
        assert mime_type == "image/jpeg"
        return self.result


def test_valid_receipt_is_auto_verified(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    term = create_active_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    payment = db.mark_qr_issued(conn, payment["id"])
    extracted = ExtractedPayment(
        readable=True,
        is_success_screen=True,
        amount_cents=5,
        recipient="Singapore University of Technology and Design",
        billing_id=SCHOOL_BILL_NUMBER,
        payment_timestamp=datetime.now(SINGAPORE_TIME).isoformat(),
        transaction_id="TX-VALID-1",
    )
    update = make_update()
    photo = MagicMock(file_id="FILE1", file_size=100)
    update.message.photo = [photo]
    context = make_context(conn, FakeExtractor(extracted))
    telegram_file = MagicMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"receipt-image"))
    context.bot.get_file.return_value = telegram_file

    asyncio.run(bot.on_receipt(update, context))

    saved = db.get_payment(conn, payment["id"])
    assert saved["status"] == "verified"
    assert saved["verified_by"] == "auto"
    assert saved["bank_txn_id"] == "TXVALID1"
    assert "accepted" in reply_text_of(update)


def test_wrong_receipt_notifies_treasurer(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    db.ensure_treasurer(conn, 999)
    term = create_active_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    payment = db.mark_qr_issued(conn, payment["id"])
    extracted = ExtractedPayment(
        readable=True,
        is_success_screen=True,
        amount_cents=500,
        recipient="Someone Else",
        billing_id="WRONG",
        payment_timestamp=datetime.now(SINGAPORE_TIME).isoformat(),
        transaction_id="TX-WRONG-1",
    )
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE2", file_size=100)]
    context = make_context(conn, FakeExtractor(extracted))
    telegram_file = MagicMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"receipt-image"))
    context.bot.get_file.return_value = telegram_file

    asyncio.run(bot.on_receipt(update, context))

    assert db.get_payment(conn, payment["id"])["status"] == "exception"
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 999


def test_duplicate_transaction_is_flagged_without_database_error(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    db.add_member(
        conn, telegram_user_id=222, full_name="Bob Lim", sutd_id="1007655", username="bob"
    )
    db.ensure_treasurer(conn, 999)
    term = create_active_term(conn)
    alice = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    alice = db.mark_qr_issued(conn, alice["id"])
    assert db.reserve_receipt_image(
        conn, payment_id=alice["id"], image_hash="old-hash"
    )
    db.mark_payment_pending(
        conn, alice["id"], screenshot_file_id="OLD", image_hash="old-hash"
    )
    assert db.reserve_bank_transaction(
        conn,
        payment_id=alice["id"],
        image_hash="old-hash",
        bank_txn_id="SHAREDTX",
    )
    db.save_verification_result(
        conn,
        alice["id"],
        status="verified",
        amount_cents=5,
        extracted_json="{}",
        bank_txn_id="SHAREDTX",
        verified_by="auto",
    )
    bob = db.get_or_create_payment(conn, member_id=222, term_id=term["id"])
    bob = db.mark_qr_issued(conn, bob["id"])
    extracted = ExtractedPayment(
        readable=True,
        is_success_screen=True,
        amount_cents=5,
        recipient="Singapore University of Technology and Design",
        billing_id=SCHOOL_BILL_NUMBER,
        payment_timestamp=datetime.now(SINGAPORE_TIME).isoformat(),
        transaction_id="SHARED-TX",
    )
    update = make_update(user_id=222, username="bob")
    update.message.photo = [MagicMock(file_id="FILE3", file_size=100)]
    context = make_context(conn, FakeExtractor(extracted))
    telegram_file = MagicMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"receipt-image"))
    context.bot.get_file.return_value = telegram_file

    asyncio.run(bot.on_receipt(update, context))

    saved = db.get_payment(conn, bob["id"])
    assert saved["status"] == "exception"
    assert saved["bank_txn_id"] is None


def test_receipt_requires_pay_command_first(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    create_active_term(conn)
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE4", file_size=100)]
    asyncio.run(bot.on_receipt(update, make_context(conn, MagicMock())))
    assert "Send /pay first" in reply_text_of(update)


def test_taken_sutd_id_records_relink_request(conn):
    db.add_member(
        conn, telegram_user_id=999, full_name="Bob Lim", sutd_id="1010765", username=None
    )
    context = make_context(conn)
    context.user_data["full_name"] = "Alice Tan"
    update = make_update(user_id=222, text="1010765", username="alice_new")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.ASK_SUTD_ID
    request = db.get_relink_request(conn, "1010765")
    assert request is not None
    assert request["new_telegram_user_id"] == 222
    assert request["new_username"] == "alice_new"


def test_receipt_blocked_by_rate_limiter(conn):
    from clubbot import ops

    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010765", username="alice"
    )
    term = create_active_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.mark_qr_issued(conn, payment["id"])

    # A limiter that is already saturated for this user.
    limiter = ops.RateLimiter(max_per_window=0, window_seconds=60)
    context = make_context(conn, FakeExtractor(None))
    context.bot_data["rate_limiter"] = limiter
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE9", file_size=100)]

    asyncio.run(bot.on_receipt(update, context))

    assert "wait a moment" in reply_text_of(update)
    # Blocked before any Gemini call: status untouched, no fingerprint reserved.
    assert db.get_payment(conn, payment["id"])["status"] == "awaiting_payment"
    assert context.bot.get_file.await_count == 0


def test_exact_receipt_image_cannot_be_reused(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    term = create_active_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    payment = db.mark_qr_issued(conn, payment["id"])
    assert db.reserve_receipt_image(
        conn,
        payment_id=payment["id"],
        image_hash="8e4998746c757d9ed5f2fb597c8be52ec501a71637d0cdf83a5c1068ce564f94",
    )
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE5", file_size=100)]
    context = make_context(conn, MagicMock())
    telegram_file = MagicMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"receipt-image"))
    context.bot.get_file.return_value = telegram_file

    asyncio.run(bot.on_receipt(update, context))

    assert "already been submitted" in reply_text_of(update)
