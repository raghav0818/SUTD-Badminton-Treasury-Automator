import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from clubbot import bot, db


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def make_update(user_id=111, text=None, username="alice"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def make_context(conn):
    context = MagicMock()
    context.bot_data = {"db": conn}
    context.user_data = {}
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
    assert "registered as Alice Tan" in reply_text_of(update)


def test_full_registration_flow(conn):
    context = make_context(conn)
    asyncio.run(bot.cmd_start(make_update(text="/start"), context))

    update = make_update(text="Alice Tan")
    assert asyncio.run(bot.on_name(update, context)) == bot.ASK_SUTD_ID

    update = make_update(text="1007654")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.CONFIRM
    assert "1007654" in reply_text_of(update)

    update = make_update(text="yes")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END

    member = db.get_member(conn, 111)
    assert member["full_name"] == "Alice Tan"
    assert member["sutd_id"] == "1007654"
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
        conn, telegram_user_id=999, full_name="Bob Lim", sutd_id="1007654", username=None
    )
    context = make_context(conn)
    context.user_data["full_name"] = "Alice Tan"
    update = make_update(text="1007654")
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
    assert "Fee collection hasn't started yet" in text


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
    assert "Fee collection hasn't started yet" in text
    assert "@" not in text
    assert "(SUTD ID 1007654)" in text


def test_build_application_smoke(conn):
    app = bot.build_application("1234567:TESTTOKEN", conn)
    assert app.bot_data["db"] is conn
    assert len(app.handlers[0]) == 3  # conversation + /status + /help
