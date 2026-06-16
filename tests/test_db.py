import sqlite3

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
    assert {"members", "terms", "payments", "admins", "audits", "settings"} <= tables


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
