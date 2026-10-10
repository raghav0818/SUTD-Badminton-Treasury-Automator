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


def test_relink_member_moves_rec_and_session_identity(conn):
    old_id, new_id = 111, 222
    db.add_member(
        conn, telegram_user_id=old_id, full_name="Old Name", sutd_id="1010654", username=None
    )
    db.note_rec_person(conn, old_id, "Old Name")
    db.set_rec_keep(conn, old_id, "Old Name", True)
    session = db.create_session(
        conn,
        title="Recre",
        starts_at="2026-10-20T19:00:00+08:00",
        ends_at="2026-10-20T23:00:00+08:00",
        venue="ISH 2",
        capacity=30,
        host_id=old_id,
        host_name="Old Name",
    )
    db.add_signup(conn, session["id"], old_id, "Old Name")
    db.set_session_pick(conn, session["id"], old_id, None)

    db.relink_member(
        conn,
        sutd_id="1010654",
        new_telegram_id=new_id,
        full_name="New Name",
        username="new",
    )

    assert [row["user_id"] for row in db.list_signups(conn, session["id"])] == [new_id]
    moved = db.get_session(conn, session["id"])
    assert (moved["host_id"], moved["primary_id"]) == (new_id, new_id)
    assert db.get_rec_person(conn, old_id) is None
    assert db.get_rec_person(conn, new_id)["keep"] == 1


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
    # Same payment re-claiming its own reference is fine; another payment isn't.
    assert db.reserve_bank_transaction(
        conn, payment_id=payment["id"], image_hash="HASH2", bank_txn_id="TX1"
    )
    assert not db.reserve_bank_transaction(
        conn, payment_id=payment["id"] + 999, image_hash="HASH3", bank_txn_id="TX1"
    )


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
    assert {"category", "with_shirt", "shirt_size", "opted_out_at"} <= columns
    term_columns = {
        row["name"] for row in migrated.execute("PRAGMA table_info(terms)")
    }
    assert {"start_notified_at", "reminder7_sent_at"} <= term_columns
    assert {
        "comp_fee_cents",
        "rec_fee_cents",
        "recshirt_fee_cents",
        "shirt_fee_cents",
        "deadline",
    } <= term_columns
    assert migrated.execute(
        "SELECT name FROM sqlite_master WHERE name = 'roster'"
    ).fetchone() is not None
    assert (
        migrated.execute(
            "SELECT name FROM sqlite_master WHERE name = 'receipt_fingerprints'"
        ).fetchone()
        is not None
    )


def test_migration_backfills_legacy_term_as_single_price(tmp_path):
    path = tmp_path / "legacy.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE terms (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, fee_cents INTEGER NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL, created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO terms (name, fee_cents, start_date, end_date)
        VALUES ('Old', 2000, '2026-01-01', '2026-03-01');
        """
    )
    old.commit()
    old.close()
    term = db.list_terms(db.connect(str(path)))[0]
    assert db.valid_amounts(term, "competitive") == {2000}
    assert db.valid_amounts(term, "recreational") == {2000}
    assert term["deadline"] == "2026-03-01"


# --- v2: prices, shirts, roster ---------------------------------------------------


def _priced_term(conn, **overrides):
    values = dict(
        name="Term 1",
        fee_cents=2000,
        rec_fee_cents=2500,
        recshirt_fee_cents=3000,
        shirt_fee_cents=1500,
        start_date="2026-09-01",
        end_date="2026-12-01",
        deadline="2026-09-15",
        created_by=999,
    )
    values.update(overrides)
    return db.create_term(conn, **values)


def test_term_fees_and_valid_amounts(conn):
    term = _priced_term(conn)
    assert term["fee_cents"] == term["comp_fee_cents"] == 2000
    assert db.term_fee(term, "competitive") == 2000
    assert db.term_fee(term, "competitive", with_shirt=True) == 3500
    assert db.term_fee(term, "recreational") == 2500
    assert db.term_fee(term, "recreational", with_shirt=True) == 3000
    assert db.valid_amounts(term, "competitive") == {2000, 3500}
    assert db.valid_amounts(term, "recreational") == {2500, 3000}


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"deadline": "2026-08-31"}, "deadline"),
        ({"deadline": "2026-12-02"}, "deadline"),
        ({"rec_fee_cents": 0}, "positive"),
        ({"recshirt_fee_cents": 2000}, "below the rec fee"),
        ({"shirt_fee_cents": -1}, "negative"),
    ],
)
def test_create_term_rejects_bad_prices_and_deadline(conn, overrides, error):
    with pytest.raises(ValueError, match=error):
        _priced_term(conn, **overrides)


def test_category_stored_when_payment_created(conn):
    db.replace_roster(conn, [("Alice", "1010001")])
    _member(conn, 111, "1010001", "Alice")
    _member(conn, 222, "1010002", "Bob")
    term = _priced_term(conn)
    alice = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    bob = db.get_or_create_payment(conn, member_id=222, term_id=term["id"])
    assert alice["category"] == "competitive"
    assert bob["category"] == "recreational"


def test_replace_roster_recategorises_only_unpaid_rows(conn):
    _member(conn, 111, "1010001", "Alice")
    _member(conn, 222, "1010002", "Bob")
    term = _priced_term(conn)
    db.get_or_create_payment(conn, member_id=111, term_id=term["id"])  # rec, unpaid
    db.mark_paid_manual(conn, member_id=222, term_id=term["id"])  # rec, paid
    db.replace_roster(conn, [("Alice", "1010001"), ("Bob", "1010002")])
    assert db.get_payment_for_member_term(conn, member_id=111, term_id=term["id"])[
        "category"
    ] == "competitive"
    assert db.get_payment_for_member_term(conn, member_id=222, term_id=term["id"])[
        "category"
    ] == "recreational"
    db.replace_roster(conn, [("Cara", "1010003")])  # wholesale replace
    assert db.roster_size(conn) == 1


def test_mark_paid_manual_uses_category_and_shirt_choice(conn):
    db.replace_roster(conn, [("Alice", "1010001")])
    _member(conn, 111, "1010001", "Alice")
    _member(conn, 222, "1010002", "Bob")
    term = _priced_term(conn)
    alice = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.set_shirt_size(conn, alice["id"], "XL")
    alice = db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    bob = db.mark_paid_manual(conn, member_id=222, term_id=term["id"])
    assert (alice["amount_cents"], alice["with_shirt"], alice["shirt_size"]) == (3500, 1, "XL")
    assert (bob["amount_cents"], bob["with_shirt"]) == (2500, 0)


def test_approved_exception_records_shirt_from_amount(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _priced_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.set_shirt_size(conn, payment["id"], "S")
    db.save_verification_result(
        conn, payment["id"], status="exception", amount_cents=3000,
        extracted_json="{}", bank_txn_id=None,
    )
    assert db.get_payment(conn, payment["id"])["with_shirt"] is None
    reviewed = db.review_payment(conn, payment["id"], approve=True)
    assert (reviewed["with_shirt"], reviewed["shirt_size"]) == (1, "S")


def _approve_exception(conn, payment_id, amount_cents):
    db.save_verification_result(
        conn, payment_id, status="exception", amount_cents=amount_cents,
        extracted_json="{}", bank_txn_id=None,
    )
    return db.review_payment(conn, payment_id, approve=True)


def test_approved_wrong_amount_is_not_a_shirt(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _priced_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.set_shirt_size(conn, payment["id"], "M")
    reviewed = _approve_exception(conn, payment["id"], 2000)  # rec underpaid
    assert (reviewed["amount_cents"], reviewed["with_shirt"], reviewed["shirt_size"]) == (
        2000, 0, None
    )


def test_approved_without_extracted_amount_uses_pay_flow_choice(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _priced_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.set_shirt_size(conn, payment["id"], "M")
    reviewed = _approve_exception(conn, payment["id"], None)  # Gemini never read it
    assert (reviewed["amount_cents"], reviewed["with_shirt"], reviewed["shirt_size"]) == (
        3000, 1, "M"
    )


def test_term_without_shirt_never_records_one(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _priced_term(conn, recshirt_fee_cents=2500, shirt_fee_cents=0)
    assert not db.offers_shirt(term, "recreational")
    assert not db.offers_shirt(term, "competitive")
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.set_shirt_size(conn, payment["id"], "M")
    assert _approve_exception(conn, payment["id"], 2500)["with_shirt"] == 0


def test_replace_roster_keeps_category_once_a_qr_is_out(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _priced_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.mark_qr_issued(conn, payment["id"])  # holding a S$25 rec QR
    db.replace_roster(conn, [("Alice", "1010001")])
    assert db.get_payment(conn, payment["id"])["category"] == "recreational"


def test_replacing_a_retry_waiting_receipt_frees_its_fingerprint(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _priced_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash="A")
    db.mark_payment_pending(conn, payment["id"], screenshot_file_id="fa", image_hash="A")
    db.record_extract_failure(conn, payment["id"])
    db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash="B")
    db.mark_payment_pending(conn, payment["id"], screenshot_file_id="fb", image_hash="B")
    # A was never checked, so the member can send it again later.
    assert db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash="A")


def test_stats_unpaid_excludes_opted_out(conn):
    _member(conn, 111, "1010001", "Alice")
    _member(conn, 222, "1010002", "Bob")
    term = _priced_term(conn)
    db.opt_out(conn, db.get_or_create_payment(conn, member_id=111, term_id=term["id"])["id"])
    stats = db.get_term_payment_stats(conn, term["id"])
    assert (stats["unpaid"], stats["opted_out"]) == (1, 1)


def test_set_shirt_size_rejects_unknown_size(conn):
    with pytest.raises(ValueError):
        db.set_shirt_size(conn, 1, "XXXL")


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
        "opted_out": 0,
        "exceptions": 1,
    }


def test_mark_paid_manual_sets_override(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _term(conn)
    payment = db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    assert payment["status"] == "verified"
    assert payment["verified_by"] == "manual_override"
    assert payment["amount_cents"] == 2000


def test_revoke(conn):
    _member(conn, 111, "1000001", "Alice")
    term = _term(conn)
    with pytest.raises(ValueError, match="no payment"):
        db.revoke_payment(conn, member_id=111, term_id=term["id"])
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    revoked = db.revoke_payment(conn, member_id=111, term_id=term["id"])
    assert revoked["status"] == "revoked"
    with pytest.raises(ValueError, match="only a verified"):
        db.revoke_payment(conn, member_id=111, term_id=term["id"])


def test_term_notification_stamps(conn):
    term = _term(conn)
    assert term["start_notified_at"] is None
    db.mark_term_start_notified(conn, term["id"])
    refreshed = db.get_term(conn, term["id"])
    assert refreshed["start_notified_at"] is not None
    assert [t["id"] for t in db.list_terms(conn)] == [term["id"]]


def test_claim_term_event_is_once_only(conn):
    term = _term(conn)
    assert db.claim_term_event(conn, term["id"], "remind-d3") is True
    assert db.claim_term_event(conn, term["id"], "remind-d3") is False
    assert db.claim_term_event(conn, term["id"], "lastcall") is True


def test_opted_out_member_is_not_unpaid_but_counted(conn):
    _member(conn, 111, "1000001", "Alice")
    _member(conn, 222, "1000002", "Bob")
    term = _term(conn)
    payment = db.get_or_create_payment(conn, member_id=222, term_id=term["id"])
    db.opt_out(conn, payment["id"])
    unpaid = db.list_unpaid_members(conn, term["id"])
    assert [m["telegram_user_id"] for m in unpaid] == [111]
    assert db.count_opted_out(conn, term["id"]) == 1


def test_startup_requeues_interrupted_extraction(conn):
    _member(conn, 111, "1010001", "Alice")
    term = _priced_term(conn)
    payment = db.get_or_create_payment(conn, member_id=111, term_id=term["id"])
    db.reserve_receipt_image(conn, payment_id=payment["id"], image_hash="A")
    db.mark_payment_pending(conn, payment["id"], screenshot_file_id="fa", image_hash="A")
    assert db.list_extraction_retries(conn, 4) == []  # crashed mid-check
    assert db.requeue_interrupted_extractions(conn) == 1
    assert [p["id"] for p in db.list_extraction_retries(conn, 4)] == [payment["id"]]
