"""SQLite schema (PRD §8) and queries."""

import secrets
import sqlite3
from datetime import date, datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    telegram_user_id INTEGER PRIMARY KEY,
    full_name        TEXT    NOT NULL,
    sutd_id          TEXT    NOT NULL UNIQUE,
    username         TEXT,
    joined_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    active           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS terms (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    fee_cents         INTEGER NOT NULL,
    start_date        TEXT    NOT NULL,
    end_date          TEXT    NOT NULL,
    created_by        INTEGER,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    start_notified_at TEXT,
    reminder7_sent_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id          INTEGER NOT NULL REFERENCES members(telegram_user_id),
    term_id            INTEGER NOT NULL REFERENCES terms(id),
    ref_code           TEXT    NOT NULL UNIQUE,
    status             TEXT    NOT NULL CHECK (status IN
        ('awaiting_payment','pending_verification','verified',
         'exception','rejected','revoked')),
    amount_cents       INTEGER,
    screenshot_file_id TEXT,
    extracted_json     TEXT,
    bank_txn_id        TEXT    UNIQUE,
    image_hash         TEXT,
    qr_issued_at       TEXT,
    payment_timestamp  TEXT,
    flagged_at         TEXT,
    audit_confirmed_at TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    verified_at        TEXT,
    verified_by        TEXT    CHECK (verified_by IN ('auto','treasurer','manual_override'))
);

CREATE TABLE IF NOT EXISTS admins (
    telegram_user_id INTEGER PRIMARY KEY,
    role             TEXT    NOT NULL CHECK (role IN ('treasurer','admin')),
    added_by         INTEGER,
    added_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start  TEXT,
    period_end    TEXT,
    payment_count INTEGER NOT NULL,
    result        TEXT,
    audited_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipt_fingerprints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id  INTEGER NOT NULL REFERENCES payments(id),
    image_hash  TEXT    NOT NULL UNIQUE,
    bank_txn_id TEXT    UNIQUE,
    submitted_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS relink_requests (
    sutd_id              TEXT    PRIMARY KEY,
    new_telegram_user_id INTEGER NOT NULL,
    new_username         TEXT,
    requested_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_member_term
    ON payments(member_id, term_id);
"""

# The "at most one treasurer" unique index is created in _migrate (not here),
# after a dedup pass, so an older DB that already holds two treasurer rows is
# repaired rather than crashing at startup.


def connect(path: str) -> sqlite3.Connection:
    # check_same_thread=False: the connection is created at startup but used
    # from PTB's event loop; SQLite itself is fine with this single-loop use.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Durability/robustness: WAL survives a crash mid-write better than the
    # default rollback journal; busy_timeout avoids spurious "database is locked".
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _add_missing_columns(
    conn: sqlite3.Connection, table: str, columns: dict[str, str]
) -> None:
    """ALTER TABLE ... ADD COLUMN for any column not already present."""
    existing = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to databases created by earlier project versions."""
    # Phase 2 columns.
    _add_missing_columns(
        conn,
        "payments",
        {"qr_issued_at": "TEXT", "payment_timestamp": "TEXT"},
    )
    # Phase 3 columns.
    _add_missing_columns(
        conn,
        "payments",
        {"flagged_at": "TEXT", "audit_confirmed_at": "TEXT"},
    )
    _add_missing_columns(
        conn,
        "terms",
        {"start_notified_at": "TEXT", "reminder7_sent_at": "TEXT"},
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO receipt_fingerprints
            (payment_id, image_hash, bank_txn_id)
        SELECT id, image_hash, bank_txn_id
        FROM payments
        WHERE image_hash IS NOT NULL
        """
    )
    # Phase 4: enforce a single treasurer. Repair any accidental duplicates
    # first (keep the earliest-added) so creating the unique index cannot fail.
    treasurers = conn.execute(
        "SELECT telegram_user_id FROM admins WHERE role = 'treasurer'"
        " ORDER BY added_at, telegram_user_id"
    ).fetchall()
    if len(treasurers) > 1:
        keep = treasurers[0]["telegram_user_id"]
        conn.execute(
            "UPDATE admins SET role = 'admin'"
            " WHERE role = 'treasurer' AND telegram_user_id != ?",
            (keep,),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_treasurer"
        " ON admins(role) WHERE role = 'treasurer'"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_member(conn: sqlite3.Connection, telegram_user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM members WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()


def get_member_by_sutd_id(conn: sqlite3.Connection, sutd_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM members WHERE sutd_id = ?", (sutd_id,)
    ).fetchone()


def add_member(
    conn: sqlite3.Connection,
    *,
    telegram_user_id: int,
    full_name: str,
    sutd_id: str,
    username: str | None,
) -> None:
    conn.execute(
        "INSERT INTO members (telegram_user_id, full_name, sutd_id, username)"
        " VALUES (?, ?, ?, ?)",
        (telegram_user_id, full_name, sutd_id, username),
    )
    conn.commit()


def update_username(
    conn: sqlite3.Connection, telegram_user_id: int, username: str | None
) -> None:
    conn.execute(
        "UPDATE members SET username = ? WHERE telegram_user_id = ?",
        (username, telegram_user_id),
    )
    conn.commit()


def ensure_treasurer(conn: sqlite3.Connection, telegram_user_id: int) -> None:
    """Bootstrap the treasurer role from config on first run.

    If a treasurer already exists in the DB it wins — changing treasurer is
    /transfertreasurer's job (Phase 4), not the .env file's.
    """
    row = conn.execute(
        "SELECT telegram_user_id FROM admins WHERE role = 'treasurer'"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO admins (telegram_user_id, role, added_by) VALUES (?, 'treasurer', ?)",
            (telegram_user_id, telegram_user_id),
        )
        conn.commit()


def get_role(conn: sqlite3.Connection, telegram_user_id: int) -> str | None:
    row = conn.execute(
        "SELECT role FROM admins WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()
    return row["role"] if row else None


def get_treasurer_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT telegram_user_id FROM admins WHERE role = 'treasurer'"
    ).fetchone()
    return row["telegram_user_id"] if row else None


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def create_term(
    conn: sqlite3.Connection,
    *,
    name: str,
    fee_cents: int,
    start_date: str,
    end_date: str,
    created_by: int,
) -> sqlite3.Row:
    """Create a collection term after validating its basic invariants."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if not name.strip():
        raise ValueError("term name cannot be empty")
    if fee_cents <= 0:
        raise ValueError("term fee must be positive")
    if end < start:
        raise ValueError("term end date cannot be before its start date")
    overlap = conn.execute(
        """
        SELECT id FROM terms
        WHERE start_date <= ? AND end_date >= ?
        LIMIT 1
        """,
        (end.isoformat(), start.isoformat()),
    ).fetchone()
    if overlap is not None:
        raise ValueError("term dates overlap an existing term")
    cursor = conn.execute(
        """
        INSERT INTO terms (name, fee_cents, start_date, end_date, created_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name.strip(), fee_cents, start.isoformat(), end.isoformat(), created_by),
    )
    conn.commit()
    return get_term(conn, cursor.lastrowid)


def get_term(conn: sqlite3.Connection, term_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM terms WHERE id = ?", (term_id,)).fetchone()


def get_active_term(
    conn: sqlite3.Connection, on_date: date | None = None
) -> sqlite3.Row | None:
    day = (on_date or date.today()).isoformat()
    return conn.execute(
        """
        SELECT * FROM terms
        WHERE start_date <= ? AND end_date >= ?
        ORDER BY start_date DESC, id DESC
        LIMIT 1
        """,
        (day, day),
    ).fetchone()


def _new_ref_code(term_id: int) -> str:
    return f"BDM-{term_id}-{secrets.token_hex(5).upper()}"


def get_or_create_payment(
    conn: sqlite3.Connection, *, member_id: int, term_id: int
) -> sqlite3.Row:
    existing = get_payment_for_member_term(conn, member_id=member_id, term_id=term_id)
    if existing is not None:
        return existing
    for _ in range(5):
        try:
            cursor = conn.execute(
                """
                INSERT INTO payments (member_id, term_id, ref_code, status)
                VALUES (?, ?, ?, 'awaiting_payment')
                """,
                (member_id, term_id, _new_ref_code(term_id)),
            )
            conn.commit()
            return get_payment(conn, cursor.lastrowid)
        except sqlite3.IntegrityError:
            conn.rollback()
            existing = get_payment_for_member_term(
                conn, member_id=member_id, term_id=term_id
            )
            if existing is not None:
                return existing
    raise RuntimeError("could not allocate a unique payment reference")


def mark_qr_issued(conn: sqlite3.Connection, payment_id: int) -> sqlite3.Row:
    """Record the first QR issue time; repeated /pay calls do not move the boundary."""
    issued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE payments SET qr_issued_at = COALESCE(qr_issued_at, ?) WHERE id = ?",
        (issued_at, payment_id),
    )
    conn.commit()
    return get_payment(conn, payment_id)


def get_payment(conn: sqlite3.Connection, payment_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.*, m.full_name, m.sutd_id, m.telegram_user_id,
               t.name AS term_name, t.fee_cents, t.start_date, t.end_date
        FROM payments p
        JOIN members m ON m.telegram_user_id = p.member_id
        JOIN terms t ON t.id = p.term_id
        WHERE p.id = ?
        """,
        (payment_id,),
    ).fetchone()


def get_payment_for_member_term(
    conn: sqlite3.Connection, *, member_id: int, term_id: int
) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT id FROM payments WHERE member_id = ? AND term_id = ?",
        (member_id, term_id),
    ).fetchone()
    return get_payment(conn, row["id"]) if row else None


def get_current_payment(
    conn: sqlite3.Connection, member_id: int, on_date: date | None = None
) -> sqlite3.Row | None:
    term = get_active_term(conn, on_date)
    if term is None:
        return None
    return get_payment_for_member_term(
        conn, member_id=member_id, term_id=term["id"]
    )


def find_duplicate_submission(
    conn: sqlite3.Connection,
    *,
    image_hash: str | None = None,
    bank_txn_id: str | None = None,
    excluding_payment_id: int | None = None,
) -> sqlite3.Row | None:
    clauses: list[str] = []
    params: list[object] = []
    if image_hash:
        clauses.append("image_hash = ?")
        params.append(image_hash)
    if bank_txn_id:
        clauses.append("bank_txn_id = ?")
        params.append(bank_txn_id)
    if not clauses:
        return None
    sql = f"SELECT * FROM receipt_fingerprints WHERE ({' OR '.join(clauses)})"
    if excluding_payment_id is not None:
        sql += " AND payment_id != ?"
        params.append(excluding_payment_id)
    return conn.execute(sql + " LIMIT 1", params).fetchone()


def reserve_receipt_image(
    conn: sqlite3.Connection, *, payment_id: int, image_hash: str
) -> bool:
    """Permanently reserve an image fingerprint; false means it was used before."""
    try:
        conn.execute(
            """
            INSERT INTO receipt_fingerprints (payment_id, image_hash)
            VALUES (?, ?)
            """,
            (payment_id, image_hash),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def reserve_bank_transaction(
    conn: sqlite3.Connection,
    *,
    payment_id: int,
    image_hash: str,
    bank_txn_id: str,
) -> bool:
    """Attach a bank reference permanently; false means another receipt used it."""
    try:
        cursor = conn.execute(
            """
            UPDATE receipt_fingerprints
            SET bank_txn_id = ?
            WHERE payment_id = ? AND image_hash = ?
            """,
            (bank_txn_id, payment_id, image_hash),
        )
        if cursor.rowcount != 1:
            raise ValueError("receipt fingerprint was not reserved")
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def release_receipt_image(
    conn: sqlite3.Connection, *, payment_id: int, image_hash: str
) -> None:
    """Allow retry when extraction failed before any bank reference was recorded."""
    conn.execute(
        """
        DELETE FROM receipt_fingerprints
        WHERE payment_id = ? AND image_hash = ? AND bank_txn_id IS NULL
        """,
        (payment_id, image_hash),
    )
    conn.commit()


def mark_payment_pending(
    conn: sqlite3.Connection,
    payment_id: int,
    *,
    screenshot_file_id: str,
    image_hash: str,
) -> None:
    conn.execute(
        """
        UPDATE payments
        SET status = 'pending_verification', screenshot_file_id = ?,
            image_hash = ?, extracted_json = NULL, bank_txn_id = NULL,
            verified_at = NULL, verified_by = NULL
        WHERE id = ?
        """,
        (screenshot_file_id, image_hash, payment_id),
    )
    conn.commit()


def reset_payment_for_retry(conn: sqlite3.Connection, payment_id: int) -> None:
    conn.execute(
        """
        UPDATE payments
        SET status = 'awaiting_payment', screenshot_file_id = NULL,
            image_hash = NULL, extracted_json = NULL, bank_txn_id = NULL
        WHERE id = ?
        """,
        (payment_id,),
    )
    conn.commit()


def save_verification_result(
    conn: sqlite3.Connection,
    payment_id: int,
    *,
    status: str,
    amount_cents: int | None,
    extracted_json: str,
    bank_txn_id: str | None,
    payment_timestamp: str | None = None,
    verified_by: str | None = None,
) -> None:
    if status not in {"pending_verification", "verified", "exception", "rejected"}:
        raise ValueError("invalid verification status")
    verified_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds")
        if status == "verified"
        else None
    )
    conn.execute(
        """
        UPDATE payments
        SET status = ?, amount_cents = ?, extracted_json = ?,
            bank_txn_id = ?, payment_timestamp = ?,
            verified_at = ?, verified_by = ?
        WHERE id = ?
        """,
        (
            status,
            amount_cents,
            extracted_json,
            bank_txn_id,
            payment_timestamp,
            verified_at,
            verified_by,
            payment_id,
        ),
    )
    conn.commit()


def review_payment(
    conn: sqlite3.Connection, payment_id: int, *, approve: bool
) -> sqlite3.Row:
    payment = get_payment(conn, payment_id)
    if payment is None:
        raise ValueError("payment not found")
    if payment["status"] != "exception":
        raise ValueError("payment is no longer awaiting review")
    status = "verified" if approve else "rejected"
    verified_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds") if approve else None
    )
    conn.execute(
        """
        UPDATE payments
        SET status = ?, verified_at = ?, verified_by = ?
        WHERE id = ?
        """,
        (status, verified_at, "treasurer" if approve else None, payment_id),
    )
    conn.commit()
    return get_payment(conn, payment_id)


# --- Phase 3: lifecycle, reminders, and auditing -------------------------------


def list_members(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM members ORDER BY full_name").fetchall()


def list_active_members(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM members WHERE active = 1 ORDER BY full_name"
    ).fetchall()


def list_unpaid_members(
    conn: sqlite3.Connection, term_id: int
) -> list[sqlite3.Row]:
    """Active members with no 'verified' payment for the term (the reminder set)."""
    return conn.execute(
        """
        SELECT * FROM members
        WHERE active = 1
          AND telegram_user_id NOT IN (
              SELECT member_id FROM payments
              WHERE term_id = ? AND status = 'verified'
          )
        ORDER BY full_name
        """,
        (term_id,),
    ).fetchall()


def get_term_payment_stats(conn: sqlite3.Connection, term_id: int) -> dict[str, int]:
    """Headline counts for /stats. 'unpaid' = active members without a verified payment."""
    registered = conn.execute(
        "SELECT COUNT(*) AS n FROM members WHERE active = 1"
    ).fetchone()["n"]
    paid = conn.execute(
        "SELECT COUNT(*) AS n FROM payments WHERE term_id = ? AND status = 'verified'",
        (term_id,),
    ).fetchone()["n"]
    exceptions = conn.execute(
        "SELECT COUNT(*) AS n FROM payments WHERE term_id = ? AND status = 'exception'",
        (term_id,),
    ).fetchone()["n"]
    flagged = conn.execute(
        "SELECT COUNT(*) AS n FROM payments"
        " WHERE term_id = ? AND flagged_at IS NOT NULL",
        (term_id,),
    ).fetchone()["n"]
    return {
        "registered": registered,
        "paid": paid,
        "unpaid": max(registered - paid, 0),
        "exceptions": exceptions,
        "flagged": flagged,
    }


def mark_paid_manual(
    conn: sqlite3.Connection, *, member_id: int, term_id: int
) -> sqlite3.Row:
    """Treasurer override for cash / off-Telegram payments (logged as manual_override)."""
    payment = get_or_create_payment(conn, member_id=member_id, term_id=term_id)
    term = get_term(conn, term_id)
    conn.execute(
        """
        UPDATE payments
        SET status = 'verified', amount_cents = ?, verified_at = ?,
            verified_by = 'manual_override', flagged_at = NULL
        WHERE id = ?
        """,
        (term["fee_cents"], _utc_now(), payment["id"]),
    )
    conn.commit()
    return get_payment(conn, payment["id"])


def flag_payment(
    conn: sqlite3.Connection, *, member_id: int, term_id: int
) -> sqlite3.Row:
    """Mark a member's payment as unverified-against-FLYMAX. No member impact."""
    payment = get_payment_for_member_term(conn, member_id=member_id, term_id=term_id)
    if payment is None:
        raise ValueError("this member has no payment for the active term")
    conn.execute(
        "UPDATE payments SET flagged_at = ? WHERE id = ?",
        (_utc_now(), payment["id"]),
    )
    conn.commit()
    return get_payment(conn, payment["id"])


def revoke_payment(
    conn: sqlite3.Connection, *, member_id: int, term_id: int
) -> sqlite3.Row:
    """Treasurer's deliberate removal of a verified membership (money never arrived)."""
    payment = get_payment_for_member_term(conn, member_id=member_id, term_id=term_id)
    if payment is None:
        raise ValueError("this member has no payment for the active term")
    if payment["status"] != "verified":
        raise ValueError("only a verified payment can be revoked")
    conn.execute(
        "UPDATE payments SET status = 'revoked' WHERE id = ?",
        (payment["id"],),
    )
    conn.commit()
    return get_payment(conn, payment["id"])


def list_unconfirmed_verified_payments(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    """Verified payments the treasurer has not yet ticked off against FLYMAX."""
    rows = conn.execute(
        """
        SELECT id FROM payments
        WHERE status = 'verified' AND audit_confirmed_at IS NULL
        ORDER BY verified_at, id
        """
    ).fetchall()
    return [get_payment(conn, row["id"]) for row in rows]


def confirm_payments_audited(
    conn: sqlite3.Connection, payment_ids: list[int]
) -> None:
    if not payment_ids:
        return
    placeholders = ",".join("?" for _ in payment_ids)
    conn.execute(
        f"UPDATE payments SET audit_confirmed_at = ?"
        f" WHERE id IN ({placeholders})",
        (_utc_now(), *payment_ids),
    )
    conn.commit()


def record_audit(
    conn: sqlite3.Connection,
    *,
    period_start: str | None,
    period_end: str | None,
    payment_count: int,
    result: str,
) -> None:
    conn.execute(
        """
        INSERT INTO audits (period_start, period_end, payment_count, result)
        VALUES (?, ?, ?, ?)
        """,
        (period_start, period_end, payment_count, result),
    )
    conn.commit()


def mark_term_start_notified(conn: sqlite3.Connection, term_id: int) -> None:
    conn.execute(
        "UPDATE terms SET start_notified_at = ? WHERE id = ?",
        (_utc_now(), term_id),
    )
    conn.commit()


def mark_term_reminder7_sent(conn: sqlite3.Connection, term_id: int) -> None:
    conn.execute(
        "UPDATE terms SET reminder7_sent_at = ? WHERE id = ?",
        (_utc_now(), term_id),
    )
    conn.commit()


def list_terms(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM terms ORDER BY start_date, id").fetchall()


# --- Phase 4: admin management, relink, and the Sheet mirror -------------------


def add_admin(
    conn: sqlite3.Connection, *, telegram_user_id: int, added_by: int
) -> None:
    """Grant a registered member the 'admin' role (idempotent)."""
    conn.execute(
        "INSERT OR IGNORE INTO admins (telegram_user_id, role, added_by)"
        " VALUES (?, 'admin', ?)",
        (telegram_user_id, added_by),
    )
    conn.commit()


def remove_admin(conn: sqlite3.Connection, telegram_user_id: int) -> bool:
    """Remove an 'admin' row. Never touches the treasurer. True if a row went."""
    cursor = conn.execute(
        "DELETE FROM admins WHERE telegram_user_id = ? AND role = 'admin'",
        (telegram_user_id,),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_admins(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All admin rows, treasurer first."""
    return conn.execute(
        "SELECT * FROM admins ORDER BY role DESC, added_at"
    ).fetchall()


def transfer_treasurer(
    conn: sqlite3.Connection, *, new_treasurer_id: int, added_by: int
) -> None:
    """Atomically demote the sitting treasurer to admin and promote the target.

    Exactly one 'treasurer' row exists afterwards. Rolls back on any error.
    """
    with conn:  # implicit transaction
        conn.execute("UPDATE admins SET role = 'admin' WHERE role = 'treasurer'")
        conn.execute(
            "INSERT INTO admins (telegram_user_id, role, added_by)"
            " VALUES (?, 'treasurer', ?)"
            " ON CONFLICT(telegram_user_id) DO UPDATE SET role = 'treasurer'",
            (new_treasurer_id, added_by),
        )


def upsert_relink_request(
    conn: sqlite3.Connection,
    *,
    sutd_id: str,
    new_telegram_user_id: int,
    new_username: str | None,
) -> None:
    """Record (or replace) the latest 'I am this SUTD member on a new account' note."""
    conn.execute(
        "INSERT INTO relink_requests (sutd_id, new_telegram_user_id, new_username)"
        " VALUES (?, ?, ?)"
        " ON CONFLICT(sutd_id) DO UPDATE SET"
        "   new_telegram_user_id = excluded.new_telegram_user_id,"
        "   new_username = excluded.new_username,"
        "   requested_at = datetime('now')",
        (sutd_id, new_telegram_user_id, new_username),
    )
    conn.commit()


def get_relink_request(
    conn: sqlite3.Connection, sutd_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM relink_requests WHERE sutd_id = ?", (sutd_id,)
    ).fetchone()


def delete_relink_request(conn: sqlite3.Connection, sutd_id: str) -> None:
    conn.execute("DELETE FROM relink_requests WHERE sutd_id = ?", (sutd_id,))
    conn.commit()


def reassign_member_telegram_id(
    conn: sqlite3.Connection, *, old_id: int, new_id: int, new_username: str | None
) -> None:
    """Move a member (and their payment/admin history) to a new Telegram id.

    The member PK is the Telegram id, and payments/admins reference it, so the
    swap needs foreign-key enforcement off briefly. Toggled outside a
    transaction (a PRAGMA inside one is ignored), then restored in `finally`.
    """
    conn.commit()  # ensure no open transaction so the PRAGMA takes effect
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            conn.execute(
                "UPDATE members SET telegram_user_id = ?, username = ?"
                " WHERE telegram_user_id = ?",
                (new_id, new_username, old_id),
            )
            conn.execute(
                "UPDATE payments SET member_id = ? WHERE member_id = ?",
                (new_id, old_id),
            )
            conn.execute(
                "UPDATE admins SET telegram_user_id = ? WHERE telegram_user_id = ?",
                (new_id, old_id),
            )
            # Safety net: confirm no dangling references before committing.
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("reassignment would break referential integrity")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def list_all_payments(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every payment joined to its member and term — the Sheet mirror's source."""
    rows = conn.execute(
        "SELECT p.id AS id FROM payments p"
        " JOIN terms t ON t.id = p.term_id"
        " ORDER BY t.start_date, p.id"
    ).fetchall()
    return [get_payment(conn, row["id"]) for row in rows]
