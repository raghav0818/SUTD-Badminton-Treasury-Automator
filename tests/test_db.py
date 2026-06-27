import sqlite3
from datetime import date, timedelta

import pytest

from clubbot import db


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def test_schema_creates_all_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "members",
        "terms",
        "payments",
        "receipt_fingerprints",
        "admins",
        "audits",
        "settings",
    } <= tables


def test_add_and_get_member(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    member = db.get_member(conn, 111)
    assert member["full_name"] == "Alice Tan"
    assert member["sutd_id"] == "1007654"
    assert member["active"] == 1
    assert db.get_member(conn, 222) is None


def test_get_member_by_sutd_id(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username=None
    )
    assert db.get_member_by_sutd_id(conn, "1007654")["telegram_user_id"] == 111
    assert db.get_member_by_sutd_id(conn, "9999999") is None


def test_duplicate_sutd_id_rejected(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username=None
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.add_member(
            conn, telegram_user_id=222, full_name="Bob Lim", sutd_id="1007654", username=None
        )


def test_update_username(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    db.update_username(conn, 111, "alice_new")
    assert db.get_member(conn, 111)["username"] == "alice_new"
    db.update_username(conn, 111, None)
    assert db.get_member(conn, 111)["username"] is None


def test_ensure_treasurer_bootstraps_once(conn):
    db.ensure_treasurer(conn, 999)
    assert db.get_role(conn, 999) == "treasurer"
    db.ensure_treasurer(conn, 999)  # idempotent
    assert db.get_role(conn, 999) == "treasurer"
    db.ensure_treasurer(conn, 555)  # existing treasurer wins
    assert db.get_role(conn, 555) is None
    assert db.get_role(conn, 999) == "treasurer"


def test_settings_roundtrip(conn):
    assert db.get_setting(conn, "ref_strategy") is None
    db.set_setting(conn, "ref_strategy", "bill_number")
    assert db.get_setting(conn, "ref_strategy") == "bill_number"
    db.set_setting(conn, "ref_strategy", "reference_label")
    assert db.get_setting(conn, "ref_strategy") == "reference_label"


def test_create_active_term_and_payment_history(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username=None
    )
    today = date.today()
    term = db.create_term(
        conn,
        name="Payment Test",
        fee_cents=5,
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=7)).isoformat(),
        created_by=999,
    )
    assert term["fee_cents"] == 5
    assert db.get_active_term(conn)["id"] == term["id"]

    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    same_payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    assert payment["id"] == same_payment["id"]
    assert payment["ref_code"].startswith(f"BDM-{term['id']}-")
    assert payment["status"] == "awaiting_payment"


def test_overlapping_terms_are_rejected(conn):
    db.create_term(
        conn,
        name="One",
        fee_cents=5,
        start_date="2026-01-01",
        end_date="2026-01-31",
        created_by=999,
    )
    with pytest.raises(ValueError, match="overlap"):
        db.create_term(
            conn,
            name="Two",
            fee_cents=2000,
            start_date="2026-01-15",
            end_date="2026-02-15",
            created_by=999,
        )


def test_payment_review_requires_exception(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username=None
    )
    term = db.create_term(
        conn,
        name="Test",
        fee_cents=5,
        start_date="2026-01-01",
        end_date="2026-12-31",
        created_by=999,
    )
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    with pytest.raises(ValueError, match="no longer"):
        db.review_payment(conn, payment["id"], approve=True)
    db.save_verification_result(
        conn,
        payment["id"],
        status="exception",
        amount_cents=5,
        extracted_json="{}",
        bank_txn_id="TX1",
    )
    reviewed = db.review_payment(conn, payment["id"], approve=True)
    assert reviewed["status"] == "verified"
    assert reviewed["verified_by"] == "treasurer"


def test_receipt_fingerprints_are_permanent_and_global(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username=None
    )
    term = db.create_term(
        conn,
        name="Test",
        fee_cents=5,
        start_date="2026-01-01",
        end_date="2026-12-31",
        created_by=999,
    )
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    assert db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash="HASH1")
    assert not db.reserve_receipt_image(
        conn, payment_id=payment["id"], image_hash="HASH1"
    )
    assert db.reserve_bank_transaction(
        conn, payment_id=payment["id"], image_hash="HASH1", bank_txn_id="TX1"
    )

    db.reset_payment_for_retry(conn, payment["id"])

    assert not db.reserve_receipt_image(
        conn, payment_id=payment["id"], image_hash="HASH1"
    )
    assert db.find_duplicate_submission(conn, bank_txn_id="TX1") is not None


def test_connect_migrates_existing_payment_database(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE members (
            telegram_user_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL,
            sutd_id TEXT NOT NULL UNIQUE, username TEXT, joined_at TEXT NOT NULL,
            active INTEGER NOT NULL
        );
        CREATE TABLE terms (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, fee_cents INTEGER NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL, created_by INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL, term_id INTEGER NOT NULL,
            ref_code TEXT NOT NULL UNIQUE, status TEXT NOT NULL, amount_cents INTEGER,
            screenshot_file_id TEXT, extracted_json TEXT, bank_txn_id TEXT UNIQUE,
            image_hash TEXT, created_at TEXT NOT NULL, verified_at TEXT, verified_by TEXT
        );
        """
    )
    old.commit()
    old.close()

    migrated = db.connect(str(path))
    columns = {
        row["name"] for row in migrated.execute("PRAGMA table_info(payments)")
    }
    assert {"qr_issued_at", "payment_timestamp"} <= columns
    assert {"flagged_at", "audit_confirmed_at"} <= columns
    term_columns = {
        row["name"] for row in migrated.execute("PRAGMA table_info(terms)")
    }
    assert {"start_notified_at", "reminder7_sent_at"} <= term_columns
    assert (
        migrated.execute(
            "SELECT name FROM sqlite_master WHERE name = 'receipt_fingerprints'"
        ).fetchone()
        is not None
    )


# --- Phase 3 -------------------------------------------------------------------


def _term(conn, fee_cents=2000):
    today = date.today()
    return db.create_term(
        conn,
        name="Term 5",
        fee_cents=fee_cents,
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=30)).isoformat(),
        created_by=999,
    )


def _member(conn, uid, sutd_id, name="Member"):
    db.add_member(
        conn, telegram_user_id=uid, full_name=name, sutd_id=sutd_id, username=None
    )


def test_list_members_and_active(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    assert [m["telegram_user_id"] for m in db.list_members(conn)] == [111, 222]
    assert len(db.list_active_members(conn)) == 2


def test_list_unpaid_members_excludes_verified(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    unpaid = db.list_unpaid_members(conn, term["id"])
    assert [m["telegram_user_id"] for m in unpaid] == [222]


def test_term_payment_stats(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    _member(conn, 333, "1000003", "Cara")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    payment = db.get_or_create_payment(conn, member_id=222, term_id=term["id"])
    db.save_verification_result(
        conn,
        payment["id"],
        status="exception",
        amount_cents=5,
        extracted_json="{}",
        bank_txn_id=None,
    )
    stats = db.get_term_payment_stats(conn, term["id"])
    assert stats == {
        "registered": 3,
        "paid": 1,
        "unpaid": 2,
        "exceptions": 1,
        "flagged": 0,
    }


def test_mark_paid_manual_sets_override(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _term(conn)
    payment = db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    assert payment["status"] == "verified"
    assert payment["verified_by"] == "manual_override"
    assert payment["amount_cents"] == 2000


def test_flag_then_revoke(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _term(conn)
    with pytest.raises(ValueError, match="no payment"):
        db.flag_payment(conn, member_id=111, term_id=term["id"])
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    flagged = db.flag_payment(conn, member_id=111, term_id=term["id"])
    assert flagged["flagged_at"] is not None
    assert db.get_term_payment_stats(conn, term["id"])["flagged"] == 1
    revoked = db.revoke_payment(conn, member_id=111, term_id=term["id"])
    assert revoked["status"] == "revoked"
    with pytest.raises(ValueError, match="only a verified"):
        db.revoke_payment(conn, member_id=111, term_id=term["id"])


def test_audit_confirmation_watermark(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    p1 = db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    db.mark_paid_manual(conn, member_id=222, term_id=term["id"])
    assert len(db.list_unconfirmed_verified_payments(conn)) == 2
    db.confirm_payments_audited(conn, [p1["id"]])
    remaining = db.list_unconfirmed_verified_payments(conn)
    assert [p["telegram_user_id"] for p in remaining] == [222]
    db.record_audit(
        conn, period_start=None, period_end="2026-06-20", payment_count=1, result="all_found"
    )
    assert conn.execute("SELECT COUNT(*) AS n FROM audits").fetchone()["n"] == 1


def test_term_notification_stamps(conn):
    term = _term(conn)
    assert term["start_notified_at"] is None
    db.mark_term_start_notified(conn, term["id"])
    db.mark_term_reminder7_sent(conn, term["id"])
    refreshed = db.get_term(conn, term["id"])
    assert refreshed["start_notified_at"] is not None
    assert refreshed["reminder7_sent_at"] is not None
    assert [t["id"] for t in db.list_terms(conn)] == [term["id"]]


# --- Phase 4: admin management, relink, Sheet source --------------------------


def test_admin_add_list_remove(conn):
    _member(conn, 111, "1010001", "Alice")
    db.ensure_treasurer(conn, 999)
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    assert db.get_role(conn, 111) == "admin"
    assert 111 in {a["telegram_user_id"] for a in db.list_admins(conn)}
    db.add_admin(conn, telegram_user_id=111, added_by=999)  # idempotent
    assert db.get_role(conn, 111) == "admin"
    assert db.remove_admin(conn, 111) is True
    assert db.get_role(conn, 111) is None
    # never removes the treasurer
    assert db.remove_admin(conn, 999) is False
    assert db.get_role(conn, 999) == "treasurer"


def test_transfer_treasurer_is_atomic(conn):
    _member(conn, 111, "1010001", "Alice")
    db.ensure_treasurer(conn, 999)
    db.transfer_treasurer(conn, new_treasurer_id=111, added_by=999)
    assert db.get_role(conn, 111) == "treasurer"
    assert db.get_role(conn, 999) == "admin"
    assert db.get_treasurer_id(conn) == 111
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM admins WHERE role = 'treasurer'"
        ).fetchone()["n"]
        == 1
    )


def test_relink_request_roundtrip(conn):
    db.upsert_relink_request(
        conn, sutd_id="1010001", new_telegram_user_id=222, new_username="newacct"
    )
    req = db.get_relink_request(conn, "1010001")
    assert req["new_telegram_user_id"] == 222
    assert req["new_username"] == "newacct"
    db.upsert_relink_request(
        conn, sutd_id="1010001", new_telegram_user_id=333, new_username="newer"
    )
    assert db.get_relink_request(conn, "1010001")["new_telegram_user_id"] == 333
    db.delete_relink_request(conn, "1010001")
    assert db.get_relink_request(conn, "1010001") is None


def test_reassign_member_preserves_payments(conn):
    _member(conn, 111, "1010001", "Alice")
    db.update_username(conn, 111, "old")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    db.reassign_member_telegram_id(conn, old_id=111, new_id=222, new_username="new")

    assert db.get_member(conn, 111) is None
    moved = db.get_member(conn, 222)
    assert moved["sutd_id"] == "1010001"
    assert moved["username"] == "new"
    pay = db.get_payment_for_member_term(conn, member_id=222, term_id=term["id"])
    assert pay is not None and pay["status"] == "verified"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_reassign_carries_admin_role(conn):
    _member(conn, 111, "1010001", "Alice")
    db.ensure_treasurer(conn, 999)
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    db.reassign_member_telegram_id(conn, old_id=111, new_id=222, new_username=None)
    assert db.get_role(conn, 111) is None
    assert db.get_role(conn, 222) == "admin"


def test_connect_dedupes_extra_treasurers(tmp_path):
    # An older DB created before the single-treasurer index could, in theory,
    # hold two treasurer rows. connect() must repair it, not crash.
    path = tmp_path / "two_treasurers.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE admins ("
        " telegram_user_id INTEGER PRIMARY KEY, role TEXT NOT NULL,"
        " added_by INTEGER, added_at TEXT NOT NULL DEFAULT (datetime('now')));"
    )
    raw.execute("INSERT INTO admins (telegram_user_id, role) VALUES (1, 'treasurer')")
    raw.execute("INSERT INTO admins (telegram_user_id, role) VALUES (2, 'treasurer')")
    raw.commit()
    raw.close()

    healed = db.connect(str(path))
    treasurers = healed.execute(
        "SELECT telegram_user_id FROM admins WHERE role = 'treasurer'"
    ).fetchall()
    assert len(treasurers) == 1
    assert treasurers[0]["telegram_user_id"] == 1  # earliest kept
    # The unique index is now in place.
    assert healed.execute(
        "SELECT name FROM sqlite_master WHERE name = 'idx_one_treasurer'"
    ).fetchone() is not None


def test_list_all_payments_spans_terms(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _term(conn)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    payments = db.list_all_payments(conn)
    assert len(payments) == 1
    assert payments[0]["full_name"] == "Alice"
    assert payments[0]["term_name"] == "Term 5"
