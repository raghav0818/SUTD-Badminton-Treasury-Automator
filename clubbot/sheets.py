"""Read-only Google Sheet mirror of the members/payments tables (PRD §Phase 4).

The Sheet is a convenience export for the treasurer, never a source of truth:
the SQLite DB is authoritative. To keep this importable on machines without
gspread installed (and to keep tests offline), every gspread / google-auth
import is lazy and all network calls live behind injectable objects.
"""

import asyncio
import logging
import sqlite3

from . import db, format

log = logging.getLogger(__name__)

# Header rows are constants so tests and the builders agree on column order.
MEMBER_HEADER = ["Full name", "SUTD ID", "Username", "Telegram ID", "Active", "Joined"]
PAYMENT_HEADER = [
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


def member_rows(conn: sqlite3.Connection) -> list[list]:
    """Header + one row per member, ready to write to a worksheet."""
    rows: list[list] = [list(MEMBER_HEADER)]
    for m in db.list_members(conn):
        rows.append(
            [
                m["full_name"],
                m["sutd_id"],
                m["username"] or "",
                m["telegram_user_id"],
                "yes" if m["active"] else "no",
                m["joined_at"],
            ]
        )
    return rows


def payment_rows(conn: sqlite3.Connection) -> list[list]:
    """Header + one row per payment, ready to write to a worksheet."""
    rows: list[list] = [list(PAYMENT_HEADER)]
    for p in db.list_all_payments(conn):
        amount = "" if p["amount_cents"] is None else format.money(p["amount_cents"])
        rows.append(
            [
                p["full_name"],
                p["sutd_id"],
                p["term_name"],
                p["status"],
                amount,
                p["verified_by"] or "",
                p["payment_timestamp"] or "",
                "yes" if p["flagged_at"] else "",
                "yes" if p["audit_confirmed_at"] else "",
            ]
        )
    return rows


class SheetMirror:
    """Owns a Google spreadsheet handle and rewrites its worksheets in full.

    Construction either authorises a real gspread client from a service-account
    JSON file (production) or accepts an already-opened spreadsheet object
    (tests), so no Google credentials or network are needed to exercise it.
    """

    def __init__(
        self, *, service_account_json: str, sheet_id: str, client=None
    ) -> None:
        if client is not None:
            # Test / injection path: `client` is an already-opened spreadsheet.
            self._spreadsheet = client
            return
        # Lazy imports: gspread/google-auth are optional and absent in tests.
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(
            service_account_json, scopes=scopes
        )
        gc = gspread.authorize(creds)
        self._spreadsheet = gc.open_by_key(sheet_id)

    def _worksheet(self, title: str):
        """Return the named worksheet, creating it if it does not exist yet."""
        try:
            return self._spreadsheet.worksheet(title)
        except Exception:
            return self._spreadsheet.add_worksheet(title=title, rows=100, cols=26)

    def full_rebuild(self, conn: sqlite3.Connection) -> None:
        """Clear and repopulate both worksheets. Blocking (gspread is blocking)."""
        for title, builder in (("Members", member_rows), ("Payments", payment_rows)):
            ws = self._worksheet(title)
            ws.clear()
            # gspread 6.x: update(values, range_name=...) — use named args so the
            # v5->v6 argument-order swap can never silently transpose them.
            ws.update(values=builder(conn), range_name="A1")


class SheetSyncer:
    """Coalescing, failure-isolated trigger for full Sheet rebuilds.

    Many DB writes in quick succession collapse into a single debounced rebuild,
    and any rebuild failure is logged but never propagated to the caller (the
    nightly rebuild is the backstop). Each rebuild opens its OWN short-lived
    SQLite connection inside the worker thread, so the bot's main connection is
    never used from two threads at once.
    """

    def __init__(
        self, mirror: SheetMirror, db_path: str, *, debounce_seconds: float = 5.0
    ) -> None:
        self._mirror = mirror
        self._db_path = db_path
        self._debounce = debounce_seconds
        self._dirty = False
        self._task = None

    def mark_dirty(self) -> None:
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop (sync context); the nightly rebuild covers it
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._debounced_run())

    async def _debounced_run(self) -> None:
        await asyncio.sleep(self._debounce)
        await self._run_now()

    def _rebuild(self) -> None:
        """Open a private connection, rebuild, close. Runs in a worker thread."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            self._mirror.full_rebuild(conn)
        finally:
            conn.close()

    async def _run_now(self) -> None:
        # Loop so a mark that arrives mid-rebuild is not lost (it set _dirty
        # again while we were awaiting); a persistent failure ends the loop
        # because _dirty stays False unless a fresh mark sets it.
        while self._dirty:
            self._dirty = False
            try:
                await asyncio.to_thread(self._rebuild)
            except Exception:
                log.warning("Google Sheet rebuild failed; continuing", exc_info=True)


def create_mirror_from_env(
    service_account_json: str | None, sheet_id: str | None
) -> SheetMirror | None:
    """Build a mirror from config, or None if unconfigured/misconfigured.

    A bad Sheet setup must never crash startup, so any failure degrades to None.
    """
    if not service_account_json or not sheet_id:
        return None
    try:
        return SheetMirror(service_account_json=service_account_json, sheet_id=sheet_id)
    except Exception:
        log.warning("Could not initialise Google Sheet mirror; continuing", exc_info=True)
        return None
