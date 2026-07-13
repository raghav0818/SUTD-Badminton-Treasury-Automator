from datetime import date, timedelta

import pytest

from clubbot import db
from clubbot.sheets import MEMBER_HEADERS, PAYMENT_HEADERS, SheetMirror


class FakeWorksheet:
    def __init__(self, rows=1, cols=1):
        self.cleared = False
        self.data = None
        self.rows = rows
        self.cols = cols

    def resize(self, rows, cols):
        self.rows = rows
        self.cols = cols

    def clear(self):
        self.cleared = True

    def update(self, values):
        # Mimic the real API: writes beyond the grid are a 400 error.
        if len(values) > self.rows or any(len(r) > self.cols for r in values):
            raise AssertionError("update exceeds grid limits")
        self.data = values


class FakeSpreadsheet:
    def __init__(self):
        self.worksheets = {}

    def worksheet(self, title):
        if title not in self.worksheets:
            raise KeyError(title)
        return self.worksheets[title]

    def add_worksheet(self, title, rows, cols):
        self.worksheets[title] = FakeWorksheet(rows, cols)
        return self.worksheets[title]


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def seed(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1010654", username="alice"
    )
    today = date.today()
    term = db.create_term(
        conn,
        name="Term 5",
        fee_cents=2000,
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=30)).isoformat(),
        created_by=999,
    )
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])


def test_snapshot_and_push_rebuild_both_tabs(conn):
    seed(conn)
    spreadsheet = FakeSpreadsheet()
    mirror = SheetMirror(spreadsheet)

    members, payments = mirror.snapshot(conn)
    mirror.push(members, payments)

    members_tab = spreadsheet.worksheets["Members"]
    assert members_tab.cleared
    assert members_tab.data[0] == MEMBER_HEADERS
    assert members_tab.data[1][0] == "Alice Tan"
    assert members_tab.data[1][2] == "@alice"

    payments_tab = spreadsheet.worksheets["Payments"]
    assert payments_tab.data[0] == PAYMENT_HEADERS
    row = payments_tab.data[1]
    assert row[0] == "Term 5"
    assert row[3] == "verified"
    assert row[4] == "S$20.00"
    assert row[6] == "manual_override"


def test_push_reuses_existing_worksheets(conn):
    seed(conn)
    spreadsheet = FakeSpreadsheet()
    spreadsheet.add_worksheet("Members", 1, 1)
    spreadsheet.add_worksheet("Payments", 1, 1)
    existing = dict(spreadsheet.worksheets)
    mirror = SheetMirror(spreadsheet)

    mirror.push(*mirror.snapshot(conn))

    assert spreadsheet.worksheets["Members"] is existing["Members"]
    assert spreadsheet.worksheets["Payments"] is existing["Payments"]


def test_empty_database_pushes_headers_only(conn):
    mirror = SheetMirror(FakeSpreadsheet())
    members, payments = mirror.snapshot(conn)
    assert members == []
    assert payments == []
    mirror.push(members, payments)


def test_tab_created_while_empty_still_accepts_growth(conn):
    # The auto-created tab is sized to the first (empty) snapshot; later
    # syncs must resize it instead of failing with a grid-limit error.
    spreadsheet = FakeSpreadsheet()
    mirror = SheetMirror(spreadsheet)
    mirror.push(*mirror.snapshot(conn))  # creates 1-row tabs

    seed(conn)
    mirror.push(*mirror.snapshot(conn))  # must not raise

    assert len(spreadsheet.worksheets["Payments"].data) == 2
