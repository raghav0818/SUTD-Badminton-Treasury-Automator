"""SQLite schema (PRD §8) and queries."""

import sqlite3

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
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    fee_cents  INTEGER NOT NULL,
    start_date TEXT    NOT NULL,
    end_date   TEXT    NOT NULL,
    created_by INTEGER,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
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
"""


def connect(path: str) -> sqlite3.Connection:
    # check_same_thread=False: the connection is created at startup but used
    # from PTB's event loop; SQLite itself is fine with this single-loop use.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


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
