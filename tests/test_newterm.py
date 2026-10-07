import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from clubbot import db, newterm

TREASURER = 999


@pytest.fixture()
def conn():
    conn = db.connect(":memory:")
    db.ensure_treasurer(conn, TREASURER)
    return conn


@pytest.fixture()
def context(conn):
    context = MagicMock()
    context.bot_data = {"db": conn}
    context.user_data = {}
    context.args = []
    context.bot.send_message = AsyncMock()
    context.application.job_queue = None
    return context


def typed(text, user_id=TREASURER):
    update = MagicMock()
    update.callback_query = None
    update.effective_user.id = user_id
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def tapped(data, user_id=TREASURER):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


def last_sent(context) -> str:
    return context.bot.send_message.call_args.kwargs["text"]


def buttons(context) -> list[tuple[str, str]]:
    """(label, value) of the latest question's buttons."""
    markup = context.bot.send_message.call_args.kwargs["reply_markup"]
    return [(row[0].text, row[0].callback_data.split(":", 2)[2]) for row in markup.inline_keyboard]


def tap(context, value):
    """Tap a button on the latest question."""
    return tapped(f"nt:{context.user_data['nt']['key']}:{value}")


def run(handler, update, context):
    return asyncio.run(handler(update, context))


def add_last_term(conn):
    return db.create_term(
        conn,
        name="Term 1",
        start_date="2026-01-05",
        end_date="2026-04-05",
        deadline="2026-01-19",
        fee_cents=2000,
        rec_fee_cents=2500,
        recshirt_fee_cents=3000,
        shirt_fee_cents=1500,
        created_by=TREASURER,
    )


def test_wizard_with_same_prices_as_last_term_is_all_taps(conn, context):
    add_last_term(conn)
    assert run(newterm.cmd_newterm, typed("/newterm"), context) == newterm.NAME
    assert buttons(context) == [("Term 2", "suggested")]
    assert run(newterm.on_name, tap(context, "suggested"), context) == newterm.START
    assert run(newterm.on_start, typed("2026-09-07"), context) == newterm.END
    same_length = buttons(context)[0]
    assert same_length == ("Same length as last term (Sun 6 Dec 2026)", "2026-12-06")
    assert run(newterm.on_end, tap(context, same_length[1]), context) == newterm.DEADLINE
    two_weeks = buttons(context)[0][1]
    assert two_weeks == "2026-09-21"
    assert run(newterm.on_deadline, tap(context, two_weeks), context) == newterm.FEES
    assert "All same as last term" in buttons(context)[0][0]
    assert run(newterm.on_fee, tap(context, "all"), context) == newterm.CONFIRM
    assert "Name: Term 2" in last_sent(context)
    assert db.list_terms(conn)[-1]["name"] == "Term 1"  # nothing until Create
    assert run(newterm.on_confirm, tap(context, "create"), context) == ConversationHandler.END
    term = db.list_terms(conn)[-1]
    assert (term["name"], term["start_date"], term["end_date"], term["deadline"]) == (
        "Term 2", "2026-09-07", "2026-12-06", "2026-09-21"
    )
    assert newterm.last_term_fees(term) == (2000, 2500, 3000, 1500)
    assert "Term created" in last_sent(context)


def test_wizard_first_term_typed_prices_without_shirts(conn, context):
    run(newterm.cmd_newterm, typed("/newterm"), context)
    assert context.bot.send_message.call_args.kwargs["reply_markup"] is None  # no suggestion
    run(newterm.on_name, typed("Term 1"), context)
    run(newterm.on_start, typed("1 Sep 2026"), context)
    run(newterm.on_end, typed("1/12/2026"), context)
    assert run(newterm.on_deadline, typed("2026-09-15"), context) == newterm.FEES
    for amount in ("20", "$25", "S$25", "0"):
        state = run(newterm.on_fee, typed(amount), context)
    assert state == newterm.CONFIRM
    assert "No shirts this term" in last_sent(context)
    run(newterm.on_confirm, tap(context, "create"), context)
    term = db.list_terms(conn)[-1]
    assert newterm.last_term_fees(term) == (2000, 2500, 2500, 0)
    assert not db.offers_shirt(term, "competitive")


def test_wizard_rejects_bad_answers_and_stays_on_the_step(conn, context):
    run(newterm.cmd_newterm, typed("/newterm"), context)
    assert run(newterm.on_name, typed("   "), context) == newterm.NAME
    run(newterm.on_name, typed("Term 1"), context)
    assert run(newterm.on_start, typed("next week"), context) == newterm.START
    run(newterm.on_start, typed("2026-09-01"), context)
    assert run(newterm.on_end, typed("2026-08-01"), context) == newterm.END
    assert "on or after the start" in last_sent(context)
    run(newterm.on_end, typed("2026-12-01"), context)
    assert run(newterm.on_deadline, typed("2026-12-25"), context) == newterm.DEADLINE
    run(newterm.on_deadline, typed("2026-09-15"), context)
    assert run(newterm.on_fee, typed("twenty"), context) == newterm.FEES
    assert run(newterm.on_fee, typed("0"), context) == newterm.FEES  # comp must be > 0
    assert run(newterm.on_fee, typed("20.555"), context) == newterm.FEES


def test_wizard_cancel_creates_nothing(conn, context):
    add_last_term(conn)
    run(newterm.cmd_newterm, typed("/newterm"), context)
    run(newterm.on_name, typed("Term 2"), context)
    assert run(newterm.on_cancel, typed("/cancel"), context) == ConversationHandler.END
    assert "nt" not in context.user_data
    assert len(db.list_terms(conn)) == 1


def test_wizard_cancel_button_creates_nothing(conn, context):
    add_last_term(conn)
    context.user_data["nt"] = {"fees": [1, 1, 1, 0], "key": "k1"}
    assert run(newterm.on_confirm, tapped("nt:k1:cancel"), context) == ConversationHandler.END
    assert "Nothing was created" in last_sent(context)
    assert len(db.list_terms(conn)) == 1


def test_wizard_create_reports_overlap_instead_of_crashing(conn, context):
    add_last_term(conn)
    run(newterm.cmd_newterm, typed("/newterm"), context)
    run(newterm.on_name, typed("Clash"), context)
    run(newterm.on_start, typed("2026-02-01"), context)
    run(newterm.on_end, typed("2026-03-01"), context)
    run(newterm.on_deadline, typed("2026-02-10"), context)
    run(newterm.on_fee, tap(context, "all"), context)
    assert run(newterm.on_confirm, tap(context, "create"), context) == ConversationHandler.END
    assert "overlap" in last_sent(context)
    assert len(db.list_terms(conn)) == 1


def test_wizard_is_treasurer_only(conn, context):
    update = typed("/newterm", user_id=111)
    assert run(newterm.cmd_newterm, update, context) == ConversationHandler.END
    assert "Only the treasurer" in update.message.reply_text.call_args.args[0]
    assert "nt" not in context.user_data


def test_expired_wizard_button_is_answered(context):
    update = tapped("nt:abc123:2026-09-01")
    asyncio.run(newterm.on_expired(update, context))
    assert "expired" in update.callback_query.answer.call_args.args[0]


def test_helpers():
    assert newterm.next_name("Term 1") == "Term 2"
    assert newterm.next_name("AY2026 T9") == "AY2026 T10"
    assert newterm.next_name("Summer") is None
    assert newterm.parse_date("1 sep 2026") == date(2026, 9, 1)
    assert newterm.parse_date("15 September 2026") == date(2026, 9, 15)
    assert newterm.nice(date(2026, 9, 1)) == "Tue 1 Sep 2026"
    with pytest.raises(ValueError):
        newterm.parse_date("2026/13/01")


def _to_fees(conn, context, name="Term 2"):
    run(newterm.cmd_newterm, typed("/newterm"), context)
    run(newterm.on_name, typed(name), context)
    run(newterm.on_start, typed("2026-09-07"), context)
    run(newterm.on_end, typed("2026-12-06"), context)
    run(newterm.on_deadline, typed("2026-09-21"), context)


def test_old_price_buttons_cannot_override_a_typed_price(conn, context):
    add_last_term(conn)
    _to_fees(conn, context)
    all_same = tap(context, "all")  # button on the first price question
    assert run(newterm.on_fee, typed("22"), context) == newterm.FEES
    assert run(newterm.on_fee, all_same, context) == newterm.FEES  # refused
    assert "earlier step" in all_same.callback_query.answer.call_args.args[0]
    assert context.user_data["nt"]["fees"] == [2200]


def test_create_button_from_an_older_wizard_is_refused(conn, context):
    add_last_term(conn)
    _to_fees(conn, context, name="Form A")
    run(newterm.on_fee, tap(context, "all"), context)
    old_create = tap(context, "create")  # wizard A's Create button
    _to_fees(conn, context, name="Form B")  # restart: wizard B
    run(newterm.on_fee, tap(context, "all"), context)
    assert run(newterm.on_confirm, old_create, context) == newterm.CONFIRM
    assert len(db.list_terms(conn)) == 1  # nothing created


def test_wizard_whose_data_was_cleared_ends_cleanly(conn, context):
    handlers = newterm.build_handlers(None)
    on_start = handlers[0].states[newterm.START][1].callback
    context.user_data.clear()  # e.g. wiped by another flow
    assert run(on_start, typed("2026-09-07"), context) == ConversationHandler.END
    assert "expired" in last_sent(context)
