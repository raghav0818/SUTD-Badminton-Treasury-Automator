import asyncio
import io
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import zxingcpp
from PIL import Image
from telegram.constants import ChatMemberStatus
from telegram.ext import ConversationHandler

from clubbot import bot, db, paynow
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
    context.bot.set_my_commands = AsyncMock()
    context.bot.delete_my_commands = AsyncMock()
    # No real JobQueue in unit tests; /newterm's live scheduling is a no-op here
    # and is covered directly in test_scheduler.py.
    context.application.job_queue = None
    context.application.bot_data = context.bot_data
    return context


def reply_text_of(update) -> str:
    return update.message.reply_text.call_args.args[0]


def test_start_unregistered_asks_for_name(conn):
    update, context = make_update(text="/start"), make_context(conn)
    assert asyncio.run(bot.cmd_start(update, context)) == bot.ASK_NAME
    assert "full name" in reply_text_of(update).lower()


def test_start_when_registered_shows_status(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    update, context = make_update(text="/start"), make_context(conn)
    assert asyncio.run(bot.cmd_start(update, context)) == ConversationHandler.END
    assert "Registered as Alice Tan" in reply_text_of(update)


def test_full_registration_flow(conn):
    context = make_context(conn)
    asyncio.run(bot.cmd_start(make_update(text="/start"), context))

    update = make_update(text="Alice Tan")
    assert asyncio.run(bot.on_name(update, context)) == bot.ASK_SUTD_ID

    update = make_update(text="1010654")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.CONFIRM
    assert "1010654" in reply_text_of(update)

    update = make_update(text="yes")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END

    member = db.get_member(conn, 111)
    assert member["full_name"] == "Alice Tan"
    assert member["sutd_id"] == "1010654"
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
        conn, telegram_user_id=999, full_name="Bob Lim", sutd_id="1010654", username=None
    )
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "in_club_group": True})
    update = make_update(text="1010654")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.ASK_SUTD_ID
    assert "already registered" in reply_text_of(update)


def test_confirm_no_cancels(conn):
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1010654"})
    update = make_update(text="no")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END
    assert db.get_member(conn, 111) is None


def test_confirm_gibberish_reprompts(conn):
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1010654"})
    update = make_update(text="maybe")
    assert asyncio.run(bot.on_confirm(update, context)) == bot.CONFIRM


def test_relink_registration_moves_history(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    term = create_active_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.arm_relink(conn, "1010654")

    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "in_club_group": True})
    update = make_update(user_id=555, text="1010654", username="alice_new")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.CONFIRM

    update = make_update(user_id=555, text="yes", username="alice_new")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END
    assert "relinked" in reply_text_of(update).lower() or "linked" in reply_text_of(update)

    member = db.get_member(conn, 555)
    assert member["sutd_id"] == "1010654"
    assert member["username"] == "alice_new"
    assert db.get_member(conn, 111) is None
    assert db.get_payment(conn, payment["id"])["telegram_user_id"] == 555
    assert not db.relink_armed(conn, "1010654")


def test_relinked_admin_keeps_admin_menu_on_new_account(conn):
    db.ensure_treasurer(conn, 999)
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    db.arm_relink(conn, "1010654")
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "in_club_group": True})
    asyncio.run(bot.on_sutd_id(make_update(user_id=555, text="1010654"), context))
    asyncio.run(bot.on_confirm(make_update(user_id=555, text="yes"), context))
    assert db.get_role(conn, 555) == "admin"
    new_scope = context.bot.set_my_commands.call_args.kwargs["scope"]
    assert new_scope.chat_id == 555
    assert context.bot.delete_my_commands.call_args.kwargs["scope"].chat_id == 111


def test_relink_confirm_after_disarm_is_blocked(conn):
    # A squatter must not be able to park at CONFIRM and fire the relink after
    # the flag was consumed or cancelled.
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    db.arm_relink(conn, "1010654")
    context = make_context(conn)
    context.user_data.update({"full_name": "Attacker", "in_club_group": True})
    update = make_update(user_id=666, text="1010654", username="attacker")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.CONFIRM

    db.disarm_relink(conn, "1010654")  # legitimate relink completed / cancelled

    update = make_update(user_id=666, text="yes", username="attacker")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END
    assert "no longer active" in reply_text_of(update)
    assert db.get_member(conn, 111) is not None  # Alice untouched
    assert db.get_member(conn, 666) is None


def test_confirm_duplicate_race_gets_friendly_error(conn):
    # Two accounts pass on_sutd_id with the same free ID; the loser's "yes"
    # must produce a reply, not an unhandled IntegrityError.
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1010654"})
    db.add_member(
        conn, telegram_user_id=222, full_name="Bob Lim", sutd_id="1010654", username=None
    )
    update = make_update(user_id=111, text="yes")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END
    assert "could not be completed" in reply_text_of(update)


def test_edited_messages_are_dropped(conn):
    update = MagicMock()
    update.edited_message = MagicMock()
    update.edited_channel_post = None
    with pytest.raises(bot.ApplicationHandlerStop):
        asyncio.run(bot._ignore_edited(update, make_context(conn)))

    normal = MagicMock()
    normal.edited_message = None
    normal.edited_channel_post = None
    asyncio.run(bot._ignore_edited(normal, make_context(conn)))  # no raise


def test_relink_not_armed_still_blocks_duplicate_id(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    context = make_context(conn)
    context.user_data.update({"full_name": "Impostor", "in_club_group": True})
    update = make_update(user_id=555, text="1010654", username="impostor")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.ASK_SUTD_ID
    assert "already registered" in reply_text_of(update)


def test_status_unregistered(conn):
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    assert "not registered" in reply_text_of(update)


def test_status_registered(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    assert "Alice Tan" in reply_text_of(update)


def test_status_shows_username_and_next_steps(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    text = reply_text_of(update)
    assert "@alice" in text
    assert "Fee collection is not open" in text


def test_status_refreshes_changed_username(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    update = make_update(text="/status", username="alice_new")
    asyncio.run(bot.cmd_status(update, make_context(conn)))
    assert db.get_member(conn, 111)["username"] == "alice_new"
    assert "@alice_new" in reply_text_of(update)


def test_start_refreshes_changed_username(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    update = make_update(text="/start", username="alice_new")
    asyncio.run(bot.cmd_start(update, make_context(conn)))
    assert db.get_member(conn, 111)["username"] == "alice_new"


def test_status_without_username_has_no_handle(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username=None
    )
    update = make_update(text="/status", username=None)
    asyncio.run(bot.cmd_status(update, make_context(conn)))
    text = reply_text_of(update)
    assert "Fee collection is not open" in text
    assert "@" not in text
    assert "(SUTD ID 1010654)" in text


def test_build_application_smoke(conn):
    app = bot.build_application("1234567:TESTTOKEN", conn)
    assert app.bot_data["db"] is conn
    assert len(app.handlers[0]) == 42  # 38 on main + 4 rec-chat removal handlers


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


def create_priced_term(conn):
    """comp S$20 (+S$15 shirt = S$35), rec S$25, rec with shirt S$30."""
    today = date.today()
    return db.create_term(
        conn,
        name="Term 1",
        fee_cents=2000,
        rec_fee_cents=2500,
        recshirt_fee_cents=3000,
        shirt_fee_cents=1500,
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=60)).isoformat(),
        deadline=(today + timedelta(days=14)).isoformat(),
        created_by=999,
    )


def newterm_args(start, end, deadline, *, name="Term 1"):
    return name.split() + [
        start,
        end,
        f"deadline={deadline}",
        "comp=20",
        "rec=25",
        "recshirt=30",
        "shirt=15",
    ]


def test_treasurer_creates_term_with_three_prices(conn):
    db.ensure_treasurer(conn, 999)
    update = make_update(user_id=999, text="/newterm")
    context = make_context(conn)
    today = date.today()
    context.args = newterm_args(
        today.isoformat(),
        (today + timedelta(days=90)).isoformat(),
        (today + timedelta(days=14)).isoformat(),
    )
    asyncio.run(bot.cmd_newterm(update, context))
    term = db.get_active_term(conn)
    assert term["name"] == "Term 1"
    assert term["fee_cents"] == term["comp_fee_cents"] == 2000
    assert (term["rec_fee_cents"], term["recshirt_fee_cents"]) == (2500, 3000)
    assert term["shirt_fee_cents"] == 1500
    assert term["deadline"] == (today + timedelta(days=14)).isoformat()
    assert db.valid_amounts(term, "competitive") == {2000, 3500}
    assert db.valid_amounts(term, "recreational") == {2500, 3000}
    text = reply_text_of(update)
    assert "S$35.00 with shirt" in text and "S$30.00 with shirt" in text


def test_parse_newterm_args_accepts_any_option_order():
    parsed = bot.parse_newterm_args(
        ["shirt=15", "Term", "1", "2026-09-01", "comp=20.50", "2026-12-01",
         "rec=25", "recshirt=30", "deadline=2026-09-15"]
    )
    assert parsed == {
        "name": "Term 1",
        "start_date": "2026-09-01",
        "end_date": "2026-12-01",
        "deadline": "2026-09-15",
        "fee_cents": 2050,
        "rec_fee_cents": 2500,
        "recshirt_fee_cents": 3000,
        "shirt_fee_cents": 1500,
    }


@pytest.mark.parametrize(
    "args, error",
    [
        (["Term", "2026-09-01", "2026-12-01", "comp=20"], "missing option"),
        (newterm_args("2026-09-01", "2026-12-01", "2026-09-15") + ["fee=5"], "unknown option"),
        (newterm_args("2026-09-01", "2026-12-01", "2026-09-15")[2:], "need a name"),
        (["T", "2026-09-01", "2026-12-01", "deadline=2026-09-15", "comp=abc",
          "rec=25", "recshirt=30", "shirt=15"], "number"),
        (["T", "2026-09-01", "2026-12-01", "deadline=2026-09-15", "comp=0",
          "rec=25", "recshirt=30", "shirt=15"], "positive"),
    ],
)
def test_parse_newterm_args_rejects_bad_input(args, error):
    with pytest.raises(ValueError, match=error):
        bot.parse_newterm_args(args)


def test_parse_newterm_args_rejects_nan_fee_cleanly():
    args = [
        "T", "2026-09-01", "2026-12-01", "deadline=2026-09-15",
        "comp=NaN", "rec=25", "recshirt=30", "shirt=15",
    ]
    with pytest.raises(ValueError, match="finite"):
        bot.parse_newterm_args(args)


def test_newterm_rejects_deadline_outside_term_with_usage(conn):
    db.ensure_treasurer(conn, 999)
    update = make_update(user_id=999, text="/newterm")
    context = make_context(conn)
    context.args = newterm_args("2026-09-01", "2026-12-01", "2026-12-15")
    asyncio.run(bot.cmd_newterm(update, context))
    assert db.list_terms(conn) == []
    text = reply_text_of(update)
    assert "deadline must be between" in text
    assert "Usage: send /newterm on its own" in text


def test_non_treasurer_cannot_create_term(conn):
    update = make_update(user_id=111, text="/newterm")
    context = make_context(conn)
    asyncio.run(bot.cmd_newterm(update, context))
    assert db.get_active_term(conn) is None
    assert "Only the treasurer" in reply_text_of(update)


def make_callback(data, user_id=111):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def pay_context(conn):
    context = make_context(conn)
    context.bot.send_photo = AsyncMock()
    return context


def qr_amount(context) -> str:
    """The amount locked into the QR the bot just sent, as a decimal string."""
    png = context.bot.send_photo.call_args.kwargs["photo"].getvalue()
    text = zxingcpp.read_barcode(Image.open(io.BytesIO(png))).text
    return paynow.parse_tlv(text)["54"]


def button_labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def test_pay_offers_shirt_choice_at_category_prices(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    create_priced_term(conn)
    update, context = make_update(text="/pay"), make_context(conn)
    asyncio.run(bot.cmd_pay(update, context))
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert button_labels(markup) == ["With shirt (S$30.00)", "Without shirt (S$25.00)"]
    # Nothing is issued until a choice is made.
    assert db.get_current_payment(conn, 111)["qr_issued_at"] is None


def test_pay_without_shirt_issues_base_fee_qr(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    create_priced_term(conn)
    context = pay_context(conn)
    asyncio.run(bot.on_pay_shirt(make_callback("pay:shirt:no"), context))
    assert qr_amount(context) == "25.00"
    caption = context.bot.send_photo.call_args.kwargs["caption"]
    assert "S$25.00" in caption and "Billing ID" in caption
    payment = db.get_current_payment(conn, 111)
    assert payment["qr_issued_at"] is not None
    assert payment["shirt_size"] is None


def test_pay_with_shirt_asks_size_then_issues_shirt_qr(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    db.replace_roster(conn, [("Alice Tan", "1010654")])  # competitive
    create_priced_term(conn)
    context = pay_context(conn)

    update = make_callback("pay:shirt:yes")
    asyncio.run(bot.on_pay_shirt(update, context))
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert button_labels(markup) == list(db.SHIRT_SIZES)
    assert context.bot.send_photo.await_count == 0

    asyncio.run(bot.on_pay_size(make_callback("pay:size:M"), context))
    assert qr_amount(context) == "35.00"
    payment = db.get_current_payment(conn, 111)
    assert payment["category"] == "competitive"
    assert payment["shirt_size"] == "M"


def test_shirt_choice_requests_sheet_sync(conn, monkeypatch):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    create_priced_term(conn)
    context = pay_context(conn)
    requested = MagicMock()
    monkeypatch.setattr(bot.scheduler, "request_sheet_sync", requested)

    asyncio.run(bot.on_pay_size(make_callback("pay:size:M"), context))

    requested.assert_called_once_with(context.application)


def test_choosing_again_reissues_qr_but_keeps_first_issue_time(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    create_priced_term(conn)
    context = pay_context(conn)
    asyncio.run(bot.on_pay_size(make_callback("pay:size:L"), context))
    first = db.get_current_payment(conn, 111)["qr_issued_at"]
    conn.execute("UPDATE payments SET qr_issued_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    asyncio.run(bot.on_pay_shirt(make_callback("pay:shirt:no"), context))
    payment = db.get_current_payment(conn, 111)
    assert first is not None
    assert payment["qr_issued_at"] == "2000-01-01T00:00:00+00:00"  # COALESCE
    # The size stays with any shirt QR already out; the choice is what changed.
    assert (payment["wants_shirt"], payment["shirt_size"]) == (0, "L")
    assert qr_amount(context) == "25.00"


def test_pay_now_button_and_deep_link_show_shirt_choice(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    create_priced_term(conn)
    context = make_context(conn)
    asyncio.run(bot.on_pay_start(make_callback("pay:start"), context))
    markup = context.bot.send_message.call_args.kwargs["reply_markup"]
    assert "Without shirt (S$25.00)" in button_labels(markup)

    update, context = make_update(text="/start pay"), make_context(conn)
    context.args = ["pay"]
    assert asyncio.run(bot.cmd_start(update, context)) == ConversationHandler.END
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert "With shirt (S$30.00)" in button_labels(markup)


def test_pay_when_already_verified_or_no_term(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    update, context = make_update(text="/pay"), make_context(conn)
    asyncio.run(bot.cmd_pay(update, context))
    assert "not currently open" in reply_text_of(update)

    term = create_priced_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    context = pay_context(conn)
    update = make_callback("pay:size:S")
    asyncio.run(bot.on_pay_size(update, context))
    assert "already verified" in update.callback_query.edit_message_text.call_args.args[0]
    assert context.bot.send_photo.await_count == 0


def test_optout_button_stops_reminders_but_pay_still_works(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    term = create_priced_term(conn)  # no payment row yet: the button creates it
    update = make_callback(f"optout:{term['id']}")
    asyncio.run(bot.on_optout(update, make_context(conn)))
    assert "no more reminders" in update.callback_query.edit_message_text.call_args.args[0]
    assert db.get_current_payment(conn, 111)["opted_out_at"] is not None
    assert db.list_unpaid_members(conn, term["id"]) == []

    context = pay_context(conn)
    asyncio.run(bot.on_pay_shirt(make_callback("pay:shirt:no"), context))
    assert qr_amount(context) == "25.00"


def test_optout_ignored_when_already_paid(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    term = create_priced_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    update = make_callback(f"optout:{term['id']}")
    asyncio.run(bot.on_optout(update, make_context(conn)))
    assert "already verified" in update.callback_query.edit_message_text.call_args.args[0]
    assert db.get_current_payment(conn, 111)["opted_out_at"] is None


class FakeExtractor:
    def __init__(self, result):
        self.result = result

    async def extract(self, image_bytes, mime_type):
        assert image_bytes == b"receipt-image"
        assert mime_type == "image/jpeg"
        return self.result


def test_valid_receipt_is_auto_verified(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
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


def submit_priced_receipt(conn, *, amount_cents, shirt_size, on_roster=False):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    db.ensure_treasurer(conn, 999)
    if on_roster:
        db.replace_roster(conn, [("Alice Tan", "1010654")])
    term = create_priced_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.set_shirt_size(conn, payment["id"], shirt_size)
    db.mark_qr_issued(conn, payment["id"])
    extracted = ExtractedPayment(
        readable=True,
        is_success_screen=True,
        amount_cents=amount_cents,
        recipient="Singapore University of Technology and Design",
        billing_id=SCHOOL_BILL_NUMBER,
        payment_timestamp=datetime.now(SINGAPORE_TIME).isoformat(),
        transaction_id="TX-SHIRT-1",
    )
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE9", file_size=100)]
    context = make_context(conn, FakeExtractor(extracted))
    telegram_file = MagicMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"receipt-image"))
    context.bot.get_file.return_value = telegram_file
    asyncio.run(bot.on_receipt(update, context))
    return db.get_payment(conn, payment["id"])


def test_receipt_with_shirt_amount_records_shirt(conn):
    saved = submit_priced_receipt(conn, amount_cents=3500, shirt_size="L", on_roster=True)
    assert saved["status"] == "verified"
    assert (saved["with_shirt"], saved["shirt_size"]) == (1, "L")


def test_receipt_with_base_amount_clears_shirt_size(conn):
    # Chose a shirt, then paid the plain rec fee: the payment decides.
    saved = submit_priced_receipt(conn, amount_cents=2500, shirt_size="L")
    assert saved["status"] == "verified"
    assert (saved["with_shirt"], saved["shirt_size"]) == (0, None)


def test_receipt_with_other_categorys_amount_is_exception(conn):
    # S$20 is the competitive fee; a recreational member must pay 25 or 30.
    saved = submit_priced_receipt(conn, amount_cents=2000, shirt_size=None)
    assert saved["status"] == "exception"
    assert saved["with_shirt"] is None


def test_wrong_receipt_notifies_treasurer(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
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
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    db.add_member(
        conn, telegram_user_id=222, full_name="Bob Lim", sutd_id="1010655", username="bob"
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
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    create_active_term(conn)
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE4", file_size=100)]
    asyncio.run(bot.on_receipt(update, make_context(conn, MagicMock())))
    assert "Send /pay first" in reply_text_of(update)


def test_exact_receipt_image_cannot_be_reused(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
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


# --- Registration gate (club groups / roster) -----------------------------------


def gate_context(conn, *, status=None, error=False):
    """Context with both club groups configured and a stubbed getChatMember."""
    db.set_setting(conn, "rec_group_id", "-100111")
    db.set_setting(conn, "comp_group_id", "-100222")
    context = make_context(conn)
    if error:
        context.bot.get_chat_member = AsyncMock(side_effect=RuntimeError("Forbidden"))
    else:
        context.bot.get_chat_member = AsyncMock(
            return_value=MagicMock(status=status, is_member=False)
        )
    return context


def register_until_sutd_id(context, sutd_id="1010654"):
    assert asyncio.run(bot.cmd_start(make_update(text="/start"), context)) == bot.ASK_NAME
    asyncio.run(bot.on_name(make_update(text="Alice Tan"), context))
    update = make_update(text=sutd_id)
    return asyncio.run(bot.on_sutd_id(update, context)), update


def test_gate_allows_group_member(conn):
    context = gate_context(conn, status=ChatMemberStatus.MEMBER)
    assert register_until_sutd_id(context)[0] == bot.CONFIRM


def test_gate_allows_restricted_only_if_still_member(conn):
    context = gate_context(conn, status=ChatMemberStatus.RESTRICTED)
    assert register_until_sutd_id(context)[0] == ConversationHandler.END
    context = gate_context(conn, status=ChatMemberStatus.RESTRICTED)
    context.bot.get_chat_member.return_value.is_member = True
    assert register_until_sutd_id(context)[0] == bot.CONFIRM


def test_gate_allows_roster_person_outside_groups(conn):
    db.replace_roster(conn, [("Alice Tan", "1010654")])
    context = gate_context(conn, status=ChatMemberStatus.LEFT)
    assert register_until_sutd_id(context)[0] == bot.CONFIRM


def test_gate_refuses_outsider_not_on_roster(conn):
    context = gate_context(conn, status=ChatMemberStatus.LEFT)
    state, update = register_until_sutd_id(context)
    assert state == ConversationHandler.END
    assert "join the club's Telegram group" in reply_text_of(update)
    assert context.user_data == {}
    assert db.get_member(conn, 111) is None


def test_gate_api_error_counts_as_not_in_group(conn):
    context = gate_context(conn, error=True)
    assert register_until_sutd_id(context)[0] == ConversationHandler.END
    assert context.bot.get_chat_member.await_count == 2  # tried both groups


def test_gate_skipped_when_no_group_configured(conn):
    context = make_context(conn)
    context.bot.get_chat_member = AsyncMock()
    assert register_until_sutd_id(context)[0] == bot.CONFIRM
    context.bot.get_chat_member.assert_not_awaited()


# --- Pay flow right after registration ------------------------------------------


def confirm_registration(conn):
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1010654"})
    update = make_update(text="yes")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END
    return update


def test_registration_mid_term_shows_shirt_choice(conn):
    create_priced_term(conn)
    update = confirm_registration(conn)
    assert "collection is open" in update.message.reply_text.call_args_list[0].args[0]
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert button_labels(markup) == ["With shirt (S$30.00)", "Without shirt (S$25.00)"]


def test_registration_without_term_shows_no_pay_flow(conn):
    update = confirm_registration(conn)
    assert update.message.reply_text.await_count == 1
    assert "You're registered" in reply_text_of(update)


# --- Gemini failure: keep pending, retry from file_id ---------------------------


class FailingExtractor:
    def __init__(self):
        self.calls = 0

    async def extract(self, image_bytes, mime_type):
        self.calls += 1
        raise RuntimeError("Gemini 503")


def setup_receipt(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    db.ensure_treasurer(conn, 999)
    term = create_active_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    return db.mark_qr_issued(conn, payment["id"])


def valid_extraction():
    return ExtractedPayment(
        readable=True,
        is_success_screen=True,
        amount_cents=5,
        recipient="Singapore University of Technology and Design",
        billing_id=SCHOOL_BILL_NUMBER,
        payment_timestamp=datetime.now(SINGAPORE_TIME).isoformat(),
        transaction_id="TX-RETRY-1",
    )


def receipt_context(conn, extractor):
    context = make_context(conn, extractor)
    context.bot.send_photo = AsyncMock()
    telegram_file = MagicMock(file_path="photos/file_1.jpg")
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"receipt-image"))
    context.bot.get_file.return_value = telegram_file
    return context


def submit_failing_receipt(conn):
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE-R", file_size=100)]
    asyncio.run(bot.on_receipt(update, receipt_context(conn, FailingExtractor())))
    return update


IMAGE_HASH = "8e4998746c757d9ed5f2fb597c8be52ec501a71637d0cdf83a5c1068ce564f94"


def test_extraction_failure_keeps_payment_pending_with_hash_reserved(conn):
    payment = setup_receipt(conn)
    update = submit_failing_receipt(conn)
    saved = db.get_payment(conn, payment["id"])
    assert saved["status"] == "pending_verification"
    assert saved["screenshot_file_id"] == "FILE-R"
    assert saved["extract_attempts"] == 1
    assert saved["last_extract_error_at"] is not None
    assert "verify it automatically shortly" in reply_text_of(update)
    # The fingerprint stays reserved: nobody can reuse the image meanwhile.
    assert not db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash=IMAGE_HASH)
    # Resending the same image is acknowledged, not rejected as reuse.
    update = submit_failing_receipt(conn)
    assert "already have this receipt" in reply_text_of(update)


def test_retry_success_reaches_verification(conn):
    payment = setup_receipt(conn)
    submit_failing_receipt(conn)
    context = receipt_context(conn, FakeExtractor(valid_extraction()))
    asyncio.run(bot.retry_failed_extractions(context))
    saved = db.get_payment(conn, payment["id"])
    assert saved["status"] == "verified"
    assert saved["bank_txn_id"] == "TXRETRY1"
    context.bot.get_file.assert_awaited_with("FILE-R")
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 111
    assert "accepted" in context.bot.send_message.call_args.kwargs["text"]


def test_crash_stuck_pending_payment_is_not_retried(conn):
    payment = setup_receipt(conn)
    db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash=IMAGE_HASH)
    db.mark_payment_pending(conn, payment["id"], screenshot_file_id="F", image_hash=IMAGE_HASH)
    assert db.list_extraction_retries(conn, bot.MAX_EXTRACT_ATTEMPTS) == []


def test_retry_gives_up_after_four_attempts_and_notifies_treasurer_once(conn):
    payment = setup_receipt(conn)
    submit_failing_receipt(conn)  # attempt 1
    context = receipt_context(conn, FailingExtractor())
    for _ in range(5):  # attempts 2-4, then nothing is left to retry
        asyncio.run(bot.retry_failed_extractions(context))
    assert context.bot_data["extractor"].calls == 3
    saved = db.get_payment(conn, payment["id"])
    assert saved["status"] == "exception"
    assert saved["extract_attempts"] == bot.MAX_EXTRACT_ATTEMPTS
    sent = context.bot.send_message.call_args_list
    to_treasurer = [c for c in sent if c.kwargs["chat_id"] == 999]
    assert len(to_treasurer) == 1
    assert to_treasurer[0].kwargs["reply_markup"] is not None  # Approve/Reject
    assert context.bot.send_photo.call_args.kwargs["photo"] == "FILE-R"
    to_member = [c for c in sent if c.kwargs["chat_id"] == 111]
    assert len(to_member) == 1 and "treasurer" in to_member[0].kwargs["text"]
    # Still reserved after giving up, and the treasurer can approve it.
    assert not db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash=IMAGE_HASH)
    assert db.review_payment(conn, payment["id"], approve=True)["status"] == "verified"


def test_both_paths_share_the_post_extraction_step(conn, monkeypatch):
    calls = []

    async def spy(context, payment, extracted, reply):
        calls.append(payment["id"])

    monkeypatch.setattr(bot, "_finish_receipt", spy)
    payment = setup_receipt(conn)
    update = make_update()
    update.message.photo = [MagicMock(file_id="FILE-S", file_size=100)]
    extractor = FakeExtractor(valid_extraction())
    asyncio.run(bot.on_receipt(update, receipt_context(conn, extractor)))
    db.record_extract_failure(conn, payment["id"])  # as if that check had failed
    asyncio.run(bot.retry_failed_extractions(receipt_context(conn, extractor)))
    assert calls == [payment["id"], payment["id"]]


def test_parse_newterm_args_allows_a_term_without_shirts():
    parsed = bot.parse_newterm_args(
        ["T", "2026-09-01", "2026-12-01", "deadline=2026-09-15", "comp=20",
         "rec=25", "recshirt=25", "shirt=0"]
    )
    assert parsed["shirt_fee_cents"] == 0


def test_failed_retry_does_not_count_against_a_newer_receipt(conn):
    payment = setup_receipt(conn)
    submit_failing_receipt(conn)

    class ReplacedMidRetry:
        async def extract(self, image_bytes, mime_type):
            # The member sends a different receipt while Gemini is working.
            db.mark_payment_pending(
                conn, payment["id"], screenshot_file_id="FILE-NEW", image_hash="other"
            )
            raise RuntimeError("Gemini down")

    asyncio.run(bot.retry_failed_extractions(receipt_context(conn, ReplacedMidRetry())))
    saved = db.get_payment(conn, payment["id"])
    assert (saved["extract_attempts"], saved["last_extract_error_at"]) == (0, None)


def _command_update(app, chat_type):
    from telegram import Chat, Message, MessageEntity, Update, User

    message = Message(
        message_id=1,
        date=datetime.now(SINGAPORE_TIME),
        chat=Chat(id=-100 if chat_type != "private" else 111, type=chat_type),
        from_user=User(id=111, first_name="A", is_bot=False),
        text="/pay",
        entities=[MessageEntity(type="bot_command", offset=0, length=4)],
    )
    message.set_bot(app.bot)
    return Update(update_id=1, message=message)


def test_member_commands_are_ignored_in_group_chats(conn):
    from telegram import User

    app = bot.build_application("1234567:TESTTOKEN", conn)
    pay = next(
        h for h in app.handlers[0]
        if getattr(h, "commands", None) == frozenset({"pay"})
    )
    app.bot._bot_user = User(id=1234567, first_name="Bot", is_bot=True, username="testbot")
    assert pay.check_update(_command_update(app, "private"))
    assert not pay.check_update(_command_update(app, "supergroup"))


def test_relink_is_gated_like_registration(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username=None
    )
    db.arm_relink(conn, "1010654")
    context = make_context(conn)
    context.user_data.update({"full_name": "Outsider", "in_club_group": False})
    update = make_update(user_id=666, text="1010654")
    assert asyncio.run(bot.on_sutd_id(update, context)) == ConversationHandler.END
    assert db.get_member(conn, 111) is not None  # Alice's account untouched


def test_retry_gives_up_even_if_member_blocked_the_bot(conn):
    setup_receipt(conn)
    submit_failing_receipt(conn)
    context = receipt_context(conn, FailingExtractor())

    async def send(chat_id, text, **kwargs):
        if chat_id == 111:
            raise RuntimeError("Forbidden: bot was blocked by the user")

    context.bot.send_message = AsyncMock(side_effect=send)
    for _ in range(3):
        asyncio.run(bot.retry_failed_extractions(context))
    to_treasurer = [
        c for c in context.bot.send_message.call_args_list if c.kwargs["chat_id"] == 999
    ]
    assert len(to_treasurer) == 1
