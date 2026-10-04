"""Read-only Google Sheet mirror (PRD §7.6): bot -> Sheet, never back.

snapshot() reads SQLite on the event loop (fast, keeps the connection
single-threaded); push() does the slow gspread network calls and is meant to
run in a worker thread via asyncio.to_thread.
"""

from __future__ import annotations

import sqlite3

from clubbot import db
from clubbot.format import money

MEMBER_HEADERS = ["Full name", "SUTD ID", "Username", "Joined", "Active"]
PAYMENT_HEADERS = [
    "Term",
    "Member",
    "SUTD ID",
    "Status",
    "Amount",
    "Paid at",
    "Verified by",
    "Flagged",
    "Category",
    "Shirt",
    "Size",
]
SHIRT_HEADERS = ["Name", "Size"]


def _yes_no(value: int | None) -> str:
    return "" if value is None else ("yes" if value else "no")


class SheetMirror:
    def __init__(self, spreadsheet) -> None:
        self._spreadsheet = spreadsheet

    @classmethod
    def from_config(cls, service_account_file: str, sheet_id: str) -> "SheetMirror":
        import gspread

        client = gspread.service_account(filename=service_account_file)
        return cls(client.open_by_key(sheet_id))

    def snapshot(
        self, conn: sqlite3.Connection
    ) -> tuple[list[list], list[list], list[list]]:
        members = [
            [
                m["full_name"],
                m["sutd_id"],
                f"@{m['username']}" if m["username"] else "",
                m["joined_at"],
                "yes" if m["active"] else "no",
            ]
            for m in db.list_members(conn)
        ]
        payments = [
            [
                p["term_name"],
                p["full_name"],
                p["sutd_id"],
                p["status"],
                money(p["amount_cents"]) if p["amount_cents"] is not None else "",
                p["payment_timestamp"] or "",
                p["verified_by"] or "",
                "yes" if p["flagged_at"] else "",
                p["category"] or "",
                _yes_no(p["with_shirt"]),
                p["shirt_size"] or "",
            ]
            for p in db.list_payments(conn)
        ]
        # Active term's shirt orders, then a per-size count block for ordering.
        term = db.get_active_term(conn)
        orders = db.list_shirt_orders(conn, term["id"]) if term else []
        shirts = [[o["full_name"], o["shirt_size"] or "?"] for o in orders]
        shirts.append(["", ""])
        shirts += [
            [f"{size} total", sum(o["shirt_size"] == size for o in orders)]
            for size in db.SHIRT_SIZES
        ]
        return members, payments, shirts

    def push(
        self, members: list[list], payments: list[list], shirts: list[list]
    ) -> None:
        """Full rebuild of all tabs; the Sheet is a mirror, never the database."""
        self._write("Members", MEMBER_HEADERS, members)
        self._write("Payments", PAYMENT_HEADERS, payments)
        self._write("Shirts", SHIRT_HEADERS, shirts)

    def _write(self, title: str, headers: list[str], rows: list[list]) -> None:
        try:
            worksheet = self._spreadsheet.worksheet(title)
        except Exception:  # get-or-create; gspread raises WorksheetNotFound
            worksheet = self._spreadsheet.add_worksheet(
                title, rows=len(rows) + 1, cols=len(headers)
            )
        # values.update cannot write past the worksheet's grid, so grow (and
        # shrink) it to the exact mirror size before writing.
        worksheet.resize(rows=len(rows) + 1, cols=len(headers))
        worksheet.clear()
        worksheet.update([headers] + rows)
