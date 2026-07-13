import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from clubbot import admin, db


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
    context.application.job_queue = None
    context.application.bot_data = context.bot_data
    return context


def reply_text_of(update) -> str:
    return update.message.reply_text.call_args.args[0]


def make_callback_update(user_id=999):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query = MagicMock()
    update.callback_query.data = "audit:allfound"
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


# --- permission denials -------------------------------------------------------


def test_unpaid_denies_non_admin(conn):
    update, context = make_update(user_id=111), make_context(conn)
    asyncio.run(admin.cmd_unpaid(update, context))
    assert "club admins" in reply_text_of(update)


def test_stats_denies_non_admin(conn):
    update, context = make_update(user_id=111), make_context(conn)
    asyncio.run(admin.cmd_stats(update, context))
    assert "club admins" in reply_text_of(update)


def test_markpaid_denies_non_treasurer(conn):
    db.add_member(
        conn, telegram_user_id=222, full_name="Bob Lim", sutd_id="1007655", username=None
    )
    conn.execute(
        "INSERT INTO admins (telegram_user_id, role) VALUES (222, 'admin')"
    )
    conn.commit()
    update, context = make_update(user_id=222), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_markpaid(update, context))
    assert "Only the treasurer" in reply_text_of(update)


def test_flag_denies_non_treasurer(conn):
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_flag(update, context))
    assert "Only the treasurer" in reply_text_of(update)


def test_revoke_denies_non_treasurer(conn):
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_revoke(update, context))
    assert "Only the treasurer" in reply_text_of(update)


# --- read-only happy paths ----------------------------------------------------


def test_unpaid_lists_members(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_unpaid(update, context))
    text = reply_text_of(update)
    assert "Bob Lim" in text
    assert "Alice Tan" not in text


def test_unpaid_everyone_paid(conn):
    db.ensure_treasurer(conn, 999)
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_unpaid(update, context))
    assert "Everyone has paid" in reply_text_of(update)


def test_unpaid_no_active_term(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_unpaid(update, context))
    assert "not open" in reply_text_of(update)


def test_stats_reports_counts(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_stats(update, context))
    text = reply_text_of(update)
    assert "Term 5" in text
    assert "S$20.00" in text
    assert "Registered: 2" in text
    assert "Paid: 1" in text
    assert "Unpaid: 1" in text


def test_members_lists_all(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_members(update, context))
    text = reply_text_of(update)
    assert "Alice Tan" in text
    assert "@alice" in text
    assert "Bob Lim" in text


def test_members_empty(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_members(update, context))
    assert "No members registered yet" in reply_text_of(update)


# --- markpaid -----------------------------------------------------------------


def test_markpaid_verifies_and_dms(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    create_active_term(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_markpaid(update, context))

    payment = db.get_current_payment(conn, 111)
    assert payment["status"] == "verified"
    assert payment["verified_by"] == "manual_override"
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 111
    assert "S$20.00" in context.bot.send_message.call_args.kwargs["text"]


def test_markpaid_unknown_sutd_id(conn):
    db.ensure_treasurer(conn, 999)
    create_active_term(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["9999999"]
    asyncio.run(admin.cmd_markpaid(update, context))
    assert "No member" in reply_text_of(update)
    assert context.bot.send_message.await_count == 0


# --- flag ---------------------------------------------------------------------


def test_flag_sets_flagged_at_without_dm(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_flag(update, context))

    payment = db.get_current_payment(conn, 111)
    assert payment["flagged_at"] is not None
    assert context.bot.send_message.await_count == 0
    assert "Flagged" in reply_text_of(update)


def test_flag_unknown_sutd_id(conn):
    db.ensure_treasurer(conn, 999)
    create_active_term(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["9999999"]
    asyncio.run(admin.cmd_flag(update, context))
    assert "No member" in reply_text_of(update)


def test_flag_member_without_payment(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    create_active_term(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_flag(update, context))
    assert "Could not flag" in reply_text_of(update)


# --- revoke -------------------------------------------------------------------


def test_revoke_verified_payment_dms_member(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_revoke(update, context))

    payment = db.get_current_payment(conn, 111)
    assert payment["status"] == "revoked"
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 111
    assert "revoked" in context.bot.send_message.call_args.kwargs["text"]


def test_revoke_non_verified_payment(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_revoke(update, context))
    assert "Could not revoke" in reply_text_of(update)
    assert context.bot.send_message.await_count == 0


# --- remind / audit (scheduler mocked) ----------------------------------------


def test_remind_invokes_scheduler(conn, monkeypatch):
    db.ensure_treasurer(conn, 999)
    create_active_term(conn)
    mock_send = AsyncMock(return_value=3)
    monkeypatch.setattr(admin.scheduler, "send_unpaid_reminders", mock_send)
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_remind(update, context))
    mock_send.assert_awaited_once()
    assert "3 member(s)" in reply_text_of(update)


def test_remind_denies_non_treasurer(conn):
    update, context = make_update(user_id=111), make_context(conn)
    asyncio.run(admin.cmd_remind(update, context))
    assert "Only the treasurer" in reply_text_of(update)


def test_audit_sends_digest(conn, monkeypatch):
    db.ensure_treasurer(conn, 999)
    monkeypatch.setattr(
        admin.scheduler, "do_audit_digest", AsyncMock(return_value=True)
    )
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_audit(update, context))
    assert "Audit digest sent" in reply_text_of(update)


def test_audit_nothing_to_send(conn, monkeypatch):
    db.ensure_treasurer(conn, 999)
    monkeypatch.setattr(
        admin.scheduler, "do_audit_digest", AsyncMock(return_value=False)
    )
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_audit(update, context))
    assert "waiting to be audited" in reply_text_of(update)


# --- audit:allfound button ----------------------------------------------------


def test_audit_allfound_confirms_payments(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    db.mark_paid_manual(conn, member_id=222, term_id=term["id"])
    assert len(db.list_unconfirmed_verified_payments(conn)) == 2

    update, context = make_callback_update(user_id=999), make_context(conn)
    asyncio.run(admin.on_audit_allfound(update, context))

    assert db.list_unconfirmed_verified_payments(conn) == []
    audits = conn.execute("SELECT * FROM audits").fetchall()
    assert len(audits) == 1
    assert audits[0]["payment_count"] == 2
    assert audits[0]["result"] == "all_found"
    assert "2 payment(s)" in edit_text_of(update)


def test_audit_allfound_denies_non_treasurer(conn):
    update, context = make_callback_update(user_id=111), make_context(conn)
    asyncio.run(admin.on_audit_allfound(update, context))
    assert "Only the treasurer" in edit_text_of(update)
    assert conn.execute("SELECT COUNT(*) AS n FROM audits").fetchone()["n"] == 0


# --- Phase 4: admin management --------------------------------------------------


def test_addadmin_promotes_member(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_addadmin(update, context))
    assert db.get_role(conn, 111) == "admin"
    assert "now an admin" in reply_text_of(update)


def test_addadmin_rejects_existing_admin(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_addadmin(update, context))
    assert "already an admin" in reply_text_of(update)


def test_addadmin_denies_non_treasurer(conn):
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_addadmin(update, context))
    assert "Only the treasurer" in reply_text_of(update)


def test_removeadmin_demotes(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_removeadmin(update, context))
    assert db.get_role(conn, 111) is None
    assert "no longer an admin" in reply_text_of(update)


def test_removeadmin_protects_treasurer(conn):
    db.ensure_treasurer(conn, 999)
    with pytest.raises(ValueError, match="transfertreasurer"):
        db.remove_admin(conn, 999)


def test_transfertreasurer_swaps_roles_and_dms(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_transfertreasurer(update, context))
    assert db.get_role(conn, 111) == "treasurer"
    assert db.get_role(conn, 999) == "admin"
    assert db.get_treasurer_id(conn) == 111
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 111


def test_transfertreasurer_to_current_treasurer_errors(conn):
    db.ensure_treasurer(conn, 111)
    seed_members(conn)
    update, context = make_update(user_id=111), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_transfertreasurer(update, context))
    assert "Could not transfer" in reply_text_of(update)


# --- Phase 4: relink -------------------------------------------------------------


def test_relink_arms_pending_flag(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_relink(update, context))
    assert db.get_setting(conn, "relink:1007654") == "pending"
    assert "NEW Telegram account" in reply_text_of(update)


def test_relink_unknown_member(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["9999999"]
    asyncio.run(admin.cmd_relink(update, context))
    assert "No member" in reply_text_of(update)


# --- Phase 4: settings -----------------------------------------------------------


def test_settings_lists_defaults(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_settings(update, context))
    text = reply_text_of(update)
    assert "school_uen = 200913519CSL5 (default)" in text
    assert "school_bill_number" in text


def test_settings_set_and_show_override(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["school_merchant_name", "NEW", "SCHOOL", "NAME"]
    asyncio.run(admin.cmd_settings(update, context))
    assert db.get_setting(conn, "school_merchant_name") == "NEW SCHOOL NAME"

    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_settings(update, context))
    text = reply_text_of(update)
    assert "school_merchant_name = NEW SCHOOL NAME" in text
    assert "school_merchant_name = NEW SCHOOL NAME (default)" not in text


def test_settings_rejects_unknown_key(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["nonsense", "value"]
    asyncio.run(admin.cmd_settings(update, context))
    assert "Unknown setting" in reply_text_of(update)


def test_settings_denies_non_treasurer(conn):
    update, context = make_update(user_id=111), make_context(conn)
    asyncio.run(admin.cmd_settings(update, context))
    assert "Only the treasurer" in reply_text_of(update)
