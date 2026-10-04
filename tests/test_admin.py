import asyncio
from datetime import date, datetime, timedelta, timezone
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


def test_stats_breaks_down_count_times_price_and_roster(conn):
    db.ensure_treasurer(conn, 999)
    db.replace_roster(
        conn, [("Alice Tan", "1010001"), ("Bob Lim", "1010002"), ("Ghost", "1010009")]
    )
    for uid, sid in ((111, "1010001"), (222, "1010002"), (333, "1010003"), (444, "1010004")):
        db.add_member(conn, telegram_user_id=uid, full_name=f"M{uid}", sutd_id=sid, username=None)
    today = date.today()
    term = db.create_term(
        conn, name="Term 1", fee_cents=2000, rec_fee_cents=2500,
        recshirt_fee_cents=3000, shirt_fee_cents=1500,
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=60)).isoformat(), created_by=999,
    )
    shirt = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.set_shirt_size(conn, shirt["id"], "M")
    for uid in (111, 222, 333, 444):
        db.mark_paid_manual(conn, member_id=uid, term_id=term["id"])

    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_stats(update, context))
    text = reply_text_of(update)
    assert "Competitive S$20.00 × 1 = S$20.00" in text
    assert "Competitive+shirt S$35.00 × 1 = S$35.00" in text
    assert "Rec S$25.00 × 2 = S$50.00" in text
    assert "Total: S$105.00" in text
    assert "2/3 roster paid" in text


def test_breakdown_handles_unknown_amount():
    rows = [{"category": "recreational", "with_shirt": 1, "amount_cents": None, "n": 1}]
    assert admin._breakdown_lines(rows) == ["Rec+shirt (amount unknown) × 1", "Total: S$0.00"]


def test_unpaid_lists_roster_people_who_never_opened_the_bot(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.replace_roster(conn, [("Alice Tan", "1007654"), ("Zed Koh", "1010999")])
    term = create_active_term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    db.mark_paid_manual(conn, member_id=222, term_id=term["id"])
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_unpaid(update, context))
    text = reply_text_of(update)
    assert "never opened the bot" in text
    assert "Zed Koh" in text
    assert "Alice Tan" not in text


# --- roster -------------------------------------------------------------------


def test_parse_roster_accepts_good_lines_and_reports_bad_ones():
    rows, rejected = admin.parse_roster(
        "/roster Alice Tan, 1010001\n"
        "Bob Lim,1010002\n"
        "\n"
        "  Tan, Ah Kow , 1010003  \n"
        "No Id Here\n"
        "Bad Id, 1007654\n"
        "Dup Alice, 1010001\n"
    )
    assert rows == [
        ("Alice Tan", "1010001"),
        ("Bob Lim", "1010002"),
        ("Tan, Ah Kow", "1010003"),
    ]
    assert rejected == ["No Id Here", "Bad Id, 1007654", "Dup Alice, 1010001"]


def test_parse_roster_bare_command_is_empty():
    assert admin.parse_roster("/roster") == ([], [])
    assert admin.parse_roster("/roster@ShuttleBuddyBot") == ([], [])


def test_roster_replaces_list_and_reports_rejects(conn):
    db.ensure_treasurer(conn, 999)
    db.replace_roster(conn, [("Old Person", "1010555")])
    update, context = make_update(
        user_id=999, text="/roster\nAlice Tan, 1010001\nBob Lim, 1010002\noops"
    ), make_context(conn)
    asyncio.run(admin.cmd_roster(update, context))
    assert db.roster_size(conn) == 1  # one bad line: nothing changes
    text = reply_text_of(update)
    assert "NOT changed" in text and "oops" in text
    update = make_update(user_id=999, text="/roster\nAlice Tan, 1010001\nBob Lim, 1010002")
    asyncio.run(admin.cmd_roster(update, context))
    assert db.roster_size(conn) == 2
    assert "2 people" in reply_text_of(update)


def test_roster_all_bad_lines_leave_roster_untouched(conn):
    db.ensure_treasurer(conn, 999)
    db.replace_roster(conn, [("Old Person", "1010555")])
    update, context = make_update(user_id=999, text="/roster\njunk"), make_context(conn)
    asyncio.run(admin.cmd_roster(update, context))
    assert db.roster_size(conn) == 1
    assert "NOT changed" in reply_text_of(update)


def test_roster_without_lines_shows_count(conn):
    db.ensure_treasurer(conn, 999)
    db.replace_roster(conn, [("Old Person", "1010555")])
    update, context = make_update(user_id=999, text="/roster"), make_context(conn)
    asyncio.run(admin.cmd_roster(update, context))
    assert "roster size: 1" in reply_text_of(update)


def test_roster_denies_non_admin(conn):
    update, context = make_update(user_id=111, text="/roster\nA B, 1010001"), make_context(conn)
    asyncio.run(admin.cmd_roster(update, context))
    assert "club admins" in reply_text_of(update)
    assert db.roster_size(conn) == 0


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


# --- remind (scheduler mocked) ------------------------------------------------


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


def test_relink_arms_flag_with_expiry(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654"]
    asyncio.run(admin.cmd_relink(update, context))
    assert db.relink_armed(conn, "1007654")
    assert "NEW Telegram account" in reply_text_of(update)
    assert "48 hours" in reply_text_of(update)


def test_relink_no_args_lists_armed(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.arm_relink(conn, "1007654")
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_relink(update, context))
    assert "1007654" in reply_text_of(update)


def test_relink_cancel_disarms(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    db.arm_relink(conn, "1007654")
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["1007654", "cancel"]
    asyncio.run(admin.cmd_relink(update, context))
    assert not db.relink_armed(conn, "1007654")
    assert "cancelled" in reply_text_of(update)


def test_relink_flag_expires_after_ttl(conn):
    seed_members(conn)
    stale = (datetime.now(timezone.utc) - db.RELINK_TTL - timedelta(minutes=1)).isoformat(
        timespec="seconds"
    )
    db.set_setting(conn, "relink:1007654", stale)
    assert not db.relink_armed(conn, "1007654")
    assert db.list_armed_relinks(conn) == []


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


def test_settings_rejects_value_that_breaks_qr(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["school_merchant_name", "CAFÉ", "…"]  # non-ASCII breaks EMVCo
    asyncio.run(admin.cmd_settings(update, context))
    assert "Not saved" in reply_text_of(update)
    assert db.get_setting(conn, "school_merchant_name") is None


def test_settings_rejects_unknown_key(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["nonsense", "value"]
    asyncio.run(admin.cmd_settings(update, context))
    assert "Unknown setting" in reply_text_of(update)


def test_settings_group_posts_toggle(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_settings(update, context))
    assert "group_posts = on (default)" in reply_text_of(update)

    update, context = make_update(user_id=999), make_context(conn)
    context.args = ["group_posts", "maybe"]
    asyncio.run(admin.cmd_settings(update, context))
    assert db.get_setting(conn, "group_posts") is None

    context.args = ["group_posts", "OFF"]
    asyncio.run(admin.cmd_settings(update, context))
    assert db.get_setting(conn, "group_posts") == "off"


# --- /setgroup ------------------------------------------------------------------


def test_setgroup_stores_chat_id_inside_a_group(conn):
    db.ensure_treasurer(conn, 999)
    for kind, key, chat_id in (("rec", "rec_group_id", -1002), ("comp", "comp_group_id", -1001)):
        update, context = make_update(user_id=999), make_context(conn)
        update.effective_chat.type = "supergroup"
        update.effective_chat.id = chat_id
        context.args = [kind]
        context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status="administrator"))
        asyncio.run(admin.cmd_setgroup(update, context))
        assert db.get_setting(conn, key) == str(chat_id)


def test_setgroup_requires_the_bot_to_be_group_admin(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = -1002
    context.args = ["rec"]
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status="member"))
    asyncio.run(admin.cmd_setgroup(update, context))
    assert db.get_setting(conn, "rec_group_id") is None
    assert "admin" in reply_text_of(update)


def test_setgroup_rejects_private_chat_and_bad_args(conn):
    db.ensure_treasurer(conn, 999)
    update, context = make_update(user_id=999), make_context(conn)
    update.effective_chat.type = "private"
    context.args = ["rec"]
    asyncio.run(admin.cmd_setgroup(update, context))
    assert "inside the group" in reply_text_of(update)

    update.effective_chat.type = "group"
    context.args = ["vip"]
    asyncio.run(admin.cmd_setgroup(update, context))
    assert "Usage" in reply_text_of(update)
    assert db.get_setting(conn, "rec_group_id") is None


def test_setgroup_denies_non_admin(conn):
    update, context = make_update(user_id=111), make_context(conn)
    update.effective_chat.type = "group"
    context.args = ["rec"]
    asyncio.run(admin.cmd_setgroup(update, context))
    assert "club admins" in reply_text_of(update)
    assert db.get_setting(conn, "rec_group_id") is None


def test_unpaid_hides_opted_out_and_counts_them(conn):
    db.ensure_treasurer(conn, 999)
    seed_members(conn)
    term = create_active_term(conn)
    db.opt_out(conn, db.get_or_create_payment(conn, member_id=222, term_id=term["id"])["id"])
    update, context = make_update(user_id=999), make_context(conn)
    asyncio.run(admin.cmd_unpaid(update, context))
    text = reply_text_of(update)
    assert "Alice Tan" in text
    assert "Bob Lim" not in text
    assert "Opted out: 1" in text


def test_settings_denies_non_treasurer(conn):
    update, context = make_update(user_id=111), make_context(conn)
    asyncio.run(admin.cmd_settings(update, context))
    assert "Only the treasurer" in reply_text_of(update)
