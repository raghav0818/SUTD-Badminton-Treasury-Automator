import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from clubbot import db, payments, paynow_config


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
    context.args = []
    context.bot.send_message = AsyncMock()
    return context


def reply_text_of(update) -> str:
    return update.message.reply_text.call_args.args[0]


def make_callback_update(user_id=999, data="settings:confirm"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def edit_text_of(update) -> str:
    return update.callback_query.edit_message_text.call_args.args[0]


def seed_admin(conn, user_id=111):
    conn.execute(
        "INSERT INTO admins (telegram_user_id, role) VALUES (?, 'admin')", (user_id,)
    )
    conn.commit()


# --- config + set_value -------------------------------------------------------


def test_get_config_returns_school_defaults(conn):
    config = paynow_config.get_paynow_config(conn)
    assert config.uen == payments.SCHOOL_UEN
    assert config.merchant_name == payments.SCHOOL_MERCHANT_NAME
    assert config.bill_number == payments.SCHOOL_BILL_NUMBER
    assert config.recipient_match == payments.DEFAULT_RECIPIENT_MATCH


def test_get_config_uses_stored_override(conn):
    db.set_setting(conn, "merchant_name", "X")
    assert paynow_config.get_paynow_config(conn).merchant_name == "X"


def test_set_value_rejects_unknown_key(conn):
    with pytest.raises(ValueError):
        paynow_config.set_value(conn, "nope", "value")


def test_set_value_rejects_blank_value(conn):
    with pytest.raises(ValueError):
        paynow_config.set_value(conn, "merchant_name", "   ")


def test_set_value_normalises_recipient_match(conn):
    paynow_config.set_value(conn, "recipient_match", "New School!")
    assert db.get_setting(conn, "recipient_match") == "NEWSCHOOL"


# --- cmd_settings (read view) -------------------------------------------------


def test_settings_no_args_shows_current_values_to_admin(conn):
    seed_admin(conn, 111)
    update, context = make_update(user_id=111), make_context(conn)
    asyncio.run(paynow_config.cmd_settings(update, context))
    text = reply_text_of(update)
    assert payments.SCHOOL_UEN in text
    assert "paynow_uen" in text
    assert "/settings set" in text


def test_settings_denies_non_admin(conn):
    update, context = make_update(user_id=111), make_context(conn)
    asyncio.run(paynow_config.cmd_settings(update, context))
    assert "club admins" in reply_text_of(update)


# --- cmd_settings set ---------------------------------------------------------


def test_settings_set_noncritical_applies_immediately(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["set", "merchant_name", "New Name"]
    asyncio.run(paynow_config.cmd_settings(update, context))
    assert db.get_setting(conn, "merchant_name") == "New Name"
    assert "New Name" in reply_text_of(update)


def test_settings_set_critical_warns_and_stashes_without_applying(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["set", "bill_number", "ABC123"]
    asyncio.run(paynow_config.cmd_settings(update, context))
    # Not applied yet.
    assert db.get_setting(conn, "bill_number") is None
    # Stashed for confirmation.
    assert context.bot_data["pending_settings"][999] == {
        "key": "bill_number",
        "value": "ABC123",
    }
    text = reply_text_of(update)
    assert "DBS FLYMAX" in text


def test_settings_set_denies_non_treasurer(conn):
    seed_admin(conn, 111)
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["set", "merchant_name", "New Name"]
    asyncio.run(paynow_config.cmd_settings(update, context))
    assert "Only the treasurer" in reply_text_of(update)
    assert db.get_setting(conn, "merchant_name") is None


def test_settings_set_unknown_key_shows_usage(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["set", "bogus", "x"]
    asyncio.run(paynow_config.cmd_settings(update, context))
    assert "Editable keys" in reply_text_of(update)


# --- on_settings_confirm ------------------------------------------------------


def test_confirm_applies_stashed_change(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["set", "bill_number", "ABC123"]
    asyncio.run(paynow_config.cmd_settings(update, context))

    cb = make_callback_update(user_id=999, data="settings:confirm")
    asyncio.run(paynow_config.on_settings_confirm(cb, context))
    assert db.get_setting(conn, "bill_number") == "ABC123"
    assert "ABC123" in edit_text_of(cb)


def test_cancel_discards_stashed_change(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["set", "bill_number", "ABC123"]
    asyncio.run(paynow_config.cmd_settings(update, context))

    cb = make_callback_update(user_id=999, data="settings:cancel")
    asyncio.run(paynow_config.on_settings_confirm(cb, context))
    assert db.get_setting(conn, "bill_number") is None
    assert "cancelled" in edit_text_of(cb)


def test_confirm_with_nothing_pending(conn):
    db.ensure_treasurer(conn, 999)
    cb = make_callback_update(user_id=999, data="settings:confirm")
    asyncio.run(paynow_config.on_settings_confirm(cb, make_context(conn)))
    assert "No settings change is pending" in edit_text_of(cb)


def test_confirm_denies_non_treasurer(conn):
    cb = make_callback_update(user_id=111, data="settings:confirm")
    asyncio.run(paynow_config.on_settings_confirm(cb, make_context(conn)))
    assert "Only the treasurer" in edit_text_of(cb)
