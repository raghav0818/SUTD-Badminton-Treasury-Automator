"""Tests for the Google Sheet mirror. No network and no gspread import here:
the spreadsheet is faked and the syncer's mirror is a MagicMock."""

import asyncio
from unittest.mock import MagicMock

import pytest

from clubbot import db, sheets


class FakeWorksheet:
    def __init__(self):
        self.cleared = False
        self.updated = None

    def clear(self):
        self.cleared = True

    def update(self, values=None, range_name=None):
        # Mirror gspread 6.x: update(values, range_name=...).
        self.updated = values


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {}

    def worksheet(self, title):
        if title not in self.sheets:
            raise KeyError(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet()
        self.sheets[title] = ws
        return ws


@pytest.fixture()
def seeded_conn():
    """A member, an active term, and a manual ('verified') payment for them."""
    conn = db.connect(":memory:")
    db.add_member(
        conn,
        telegram_user_id=111,
        full_name="Alice Tan",
        sutd_id="1007654",
        username="alice",
    )
    term = db.create_term(
        conn,
        name="2026 Term 3",
        fee_cents=2000,
        start_date="2026-06-01",
        end_date="2026-08-31",
        created_by=999,
    )
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    return conn


# (a) row builders -------------------------------------------------------------


def test_member_rows_header_and_data(seeded_conn):
    rows = sheets.member_rows(seeded_conn)
    assert rows[0] == ["Full name", "SUTD ID", "Username", "Telegram ID", "Active", "Joined"]
    assert len(rows) == 2
    row = rows[1]
    assert row[0] == "Alice Tan"
    assert row[1] == "1007654"
    assert row[2] == "alice"
    assert row[3] == 111
    assert row[4] == "yes"


def test_member_rows_username_none_blank():
    conn = db.connect(":memory:")
    db.add_member(
        conn, telegram_user_id=222, full_name="Bob Lim", sutd_id="1009999", username=None
    )
    rows = sheets.member_rows(conn)
    assert rows[1][2] == ""


def test_payment_rows_header_and_amount(seeded_conn):
    rows = sheets.payment_rows(seeded_conn)
    assert rows[0] == [
        "Member",
        "SUTD ID",
        "Term",
        "Status",
        "Amount",
        "Verified by",
        "Payment time",
        "Flagged",
        "Audit confirmed",
    ]
    assert len(rows) == 2
    row = rows[1]
    assert row[0] == "Alice Tan"
    assert row[2] == "2026 Term 3"
    assert row[3] == "verified"
    assert row[4] == "S$20.00"
    assert row[5] == "manual_override"
    # Not flagged, not audit-confirmed yet.
    assert row[7] == ""
    assert row[8] == ""


# (b) SheetMirror.full_rebuild -------------------------------------------------


def test_full_rebuild_creates_and_populates_worksheets(seeded_conn):
    spreadsheet = FakeSpreadsheet()
    mirror = sheets.SheetMirror(
        client=spreadsheet, service_account_json="x", sheet_id="x"
    )
    mirror.full_rebuild(seeded_conn)

    assert set(spreadsheet.sheets) == {"Members", "Payments"}
    members_ws = spreadsheet.sheets["Members"]
    payments_ws = spreadsheet.sheets["Payments"]
    assert members_ws.cleared is True
    assert payments_ws.cleared is True
    assert members_ws.updated == sheets.member_rows(seeded_conn)
    assert payments_ws.updated == sheets.payment_rows(seeded_conn)


# (c) coalescing ---------------------------------------------------------------


def test_mark_dirty_coalesces_into_single_rebuild():
    mirror = MagicMock()
    syncer = sheets.SheetSyncer(mirror, db_path=":memory:", debounce_seconds=0)

    async def scenario():
        syncer.mark_dirty()
        syncer.mark_dirty()
        syncer.mark_dirty()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert mirror.full_rebuild.call_count == 1


# (d) failure isolation --------------------------------------------------------


def test_run_now_swallows_rebuild_errors():
    mirror = MagicMock()
    mirror.full_rebuild.side_effect = RuntimeError("boom")
    syncer = sheets.SheetSyncer(mirror, db_path=":memory:", debounce_seconds=0)
    syncer._dirty = True

    # Must not raise even though full_rebuild blows up.
    asyncio.run(syncer._run_now())
    mirror.full_rebuild.assert_called_once()


def test_run_now_reruns_when_marked_during_rebuild():
    # A mark that lands while a rebuild is in flight must not be lost.
    mirror = MagicMock()
    syncer = sheets.SheetSyncer(mirror, db_path=":memory:", debounce_seconds=0)
    calls = {"n": 0}

    def rebuild(conn):
        calls["n"] += 1
        if calls["n"] == 1:
            syncer._dirty = True  # simulate a DB write during the first rebuild

    mirror.full_rebuild.side_effect = rebuild
    syncer._dirty = True
    asyncio.run(syncer._run_now())
    assert calls["n"] == 2


# (e) create_mirror_from_env guards -------------------------------------------


def test_create_mirror_from_env_returns_none_when_unconfigured():
    assert sheets.create_mirror_from_env("", "") is None
    assert sheets.create_mirror_from_env("x", "") is None
    assert sheets.create_mirror_from_env("", "x") is None
    assert sheets.create_mirror_from_env(None, None) is None
