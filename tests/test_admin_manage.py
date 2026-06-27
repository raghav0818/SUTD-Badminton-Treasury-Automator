import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from clubbot import admin_manage, db


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


def make_callback_update(user_id=999, data="transfer:confirm"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def edit_text_of(update) -> str:
    return update.callback_query.edit_message_text.call_args.args[0]


def create_active_term(conn, treasurer_id=999, fee_cents=2000):
    today = date.today()
    return db.create_term(
        conn,
        name="Term 5",
        fee_cents=fee_cents,
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=30)).isoformat(),
        created_by=treasurer_id,
    )


def seed_members(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    db.add_member(
        conn, telegram_user_id=222, full_name="Bob Lim", sutd_id="1007655", username=None
    )


# --- addadmin -----------------------------------------------------------------


def test_addadmin_happy_path(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_addadmin(update, context))

    assert db.get_role(conn, 111) == "admin"
    assert "Added Alice Tan as an admin." in reply_text_of(update)
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 111


def test_addadmin_unknown_member(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["9999999"]
    asyncio.run(admin_manage.cmd_addadmin(update, context))
    assert "No member" in reply_text_of(update)
    assert context.bot.send_message.await_count == 0


def test_addadmin_already_admin(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_addadmin(update, context))
    assert "already an admin or the treasurer" in reply_text_of(update)
    assert context.bot.send_message.await_count == 0


def test_addadmin_denies_non_treasurer(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["1007655"]
    asyncio.run(admin_manage.cmd_addadmin(update, context))
    assert "Only the treasurer" in reply_text_of(update)


# --- removeadmin --------------------------------------------------------------


def test_removeadmin_removes_admin(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_removeadmin(update, context))
    assert db.get_role(conn, 111) is None
    assert "Removed Alice Tan's admin access." in reply_text_of(update)


def test_removeadmin_refuses_treasurer(conn):
    db.ensure_treasurer(conn, 999)
    db.add_member(
        conn, telegram_user_id=999, full_name="Tina Treasurer", sutd_id="1000999", username=None
    )
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1000999"]
    asyncio.run(admin_manage.cmd_removeadmin(update, context))
    assert db.get_role(conn, 999) == "treasurer"
    assert "cannot remove the treasurer" in reply_text_of(update)


def test_removeadmin_non_admin(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_removeadmin(update, context))
    assert "is not an admin" in reply_text_of(update)


# --- transfertreasurer --------------------------------------------------------


def test_transfer_confirm_makes_target_treasurer(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    context = make_context(conn)
    cmd_update = make_update(user_id=999)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_transfertreasurer(cmd_update, context))

    cb_update = make_callback_update(user_id=999, data="transfer:confirm")
    asyncio.run(admin_manage.on_transfer_confirm(cb_update, context))

    assert db.get_role(conn, 111) == "treasurer"
    assert db.get_role(conn, 999) == "admin"
    assert "Alice Tan is now the treasurer." in edit_text_of(cb_update)
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 111


def test_transfer_cancel_leaves_roles_unchanged(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    context = make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_transfertreasurer(make_update(user_id=999), context))

    cb_update = make_callback_update(user_id=999, data="transfer:cancel")
    asyncio.run(admin_manage.on_transfer_confirm(cb_update, context))

    assert db.get_role(conn, 999) == "treasurer"
    assert db.get_role(conn, 111) is None
    assert "Transfer cancelled." in edit_text_of(cb_update)
    assert context.bot.send_message.await_count == 0


def test_transfer_denies_non_treasurer(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["1007655"]
    asyncio.run(admin_manage.cmd_transfertreasurer(update, context))
    assert "Only the treasurer" in reply_text_of(update)
    assert "pending_transfer" not in context.bot_data


def test_transfer_already_treasurer(conn):
    db.ensure_treasurer(conn, 999)
    db.add_member(
        conn, telegram_user_id=999, full_name="Tina Treasurer", sutd_id="1000999", username=None
    )
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1000999"]
    asyncio.run(admin_manage.cmd_transfertreasurer(update, context))
    assert "already the treasurer" in reply_text_of(update)


# --- relink -------------------------------------------------------------------


def test_relink_explicit_id_preserves_payment(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])

    context = make_context(conn)
    context.args = ["1007654", "333"]
    asyncio.run(admin_manage.cmd_relink(make_update(user_id=999), context))

    cb_update = make_callback_update(user_id=999, data="relink:confirm")
    asyncio.run(admin_manage.on_relink_confirm(cb_update, context))

    assert db.get_member(conn, 111) is None
    assert db.get_member(conn, 333) is not None
    payment = db.get_payment_for_member_term(conn, member_id=333, term_id=term["id"])
    assert payment["status"] == "verified"
    assert "Relinked SUTD 1007654" in edit_text_of(cb_update)
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 333


def test_relink_via_pending_request(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.upsert_relink_request(
        conn, sutd_id="1007654", new_telegram_user_id=444, new_username="alice_new"
    )

    context = make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_relink(make_update(user_id=999), context))

    cb_update = make_callback_update(user_id=999, data="relink:confirm")
    asyncio.run(admin_manage.on_relink_confirm(cb_update, context))

    moved = db.get_member(conn, 444)
    assert moved is not None
    assert moved["username"] == "alice_new"
    assert db.get_member(conn, 111) is None
    assert db.get_relink_request(conn, "1007654") is None
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 444


def test_relink_no_request_and_no_explicit_id(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin_manage.cmd_relink(update, context))
    assert "No relink request found" in reply_text_of(update)


def test_relink_refuses_existing_member(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654", "222"]
    asyncio.run(admin_manage.cmd_relink(update, context))
    assert "already belongs to another member" in reply_text_of(update)
    assert "pending_relink" not in context.bot_data


def test_relink_unknown_member(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["9999999", "333"]
    asyncio.run(admin_manage.cmd_relink(update, context))
    assert "No member is registered with SUTD ID 9999999" in reply_text_of(update)


def test_relink_denies_non_treasurer(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["1007654", "333"]
    asyncio.run(admin_manage.cmd_relink(update, context))
    assert "Only the treasurer" in reply_text_of(update)
