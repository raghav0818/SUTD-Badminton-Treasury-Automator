import io
from dataclasses import dataclass
from datetime import datetime, timezone

import zxingcpp
from PIL import Image

from clubbot import paynow
from clubbot.payments import (
    SCHOOL_BILL_NUMBER,
    ExtractedPayment,
    build_member_qr,
    verify_extracted_payment,
)


@dataclass(frozen=True)
class _StubConfig:
    """Stand-in for paynow_config.PayNowConfig (duck-typed, no import cycle)."""

    uen: str = "200913519CSL5XYZ"
    merchant_name: str = "NEW SCHOOL ACCOUNT"
    bill_number: str = "NEWBILL000111"
    recipient_match: str = "NEWSCHOOLACCOUNT"


def valid_extraction(**overrides):
    values = {
        "readable": True,
        "is_success_screen": True,
        "amount_cents": 5,
        "recipient": "Singapore University of Technology and Design",
        "billing_id": SCHOOL_BILL_NUMBER,
        "payment_timestamp": "2026-06-20T10:38:00+08:00",
        "transaction_id": "TX123",
    }
    values.update(overrides)
    return ExtractedPayment(**values)


def test_member_qr_preserves_billing_id_and_uses_reference_label():
    png = build_member_qr(fee_cents=5, reference="BDM-1-ABC123")
    result = zxingcpp.read_barcode(Image.open(io.BytesIO(png)))
    root = paynow.parse_tlv(result.text)
    extra = paynow.parse_tlv(root["62"])
    assert root["54"] == "0.05"
    assert extra["01"] == SCHOOL_BILL_NUMBER
    assert extra["05"] == "BDM-1-ABC123"


def test_valid_receipt_is_verified():
    result = verify_extracted_payment(
        valid_extraction(),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
    )
    assert result.passed
    assert result.reasons == ()


def test_unreadable_or_incomplete_screen_requests_retry():
    unreadable = verify_extracted_payment(
        valid_extraction(readable=False),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
    )
    incomplete = verify_extracted_payment(
        valid_extraction(is_success_screen=False),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
    )
    assert unreadable.outcome == "retry"
    assert incomplete.outcome == "retry"


def test_wrong_fields_go_to_exception():
    result = verify_extracted_payment(
        valid_extraction(
            amount_cents=20,
            recipient="Someone Else",
            billing_id="WRONG",
            payment_timestamp="2022-01-01T10:00:00+08:00",
            transaction_id=None,
        ),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
    )
    assert result.outcome == "exception"
    assert len(result.reasons) == 6


def test_duplicate_transaction_goes_to_exception():
    result = verify_extracted_payment(
        valid_extraction(),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
        duplicate_transaction=True,
    )
    assert result.outcome == "exception"
    assert "already been submitted" in result.reasons[0]


def test_receipt_from_before_qr_issue_is_rejected():
    result = verify_extracted_payment(
        valid_extraction(payment_timestamp="2026-06-20T10:00:00+08:00"),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
    )
    assert result.outcome == "exception"
    assert "before this payment QR" in result.reasons[0]


def test_timestamp_without_timezone_is_rejected():
    result = verify_extracted_payment(
        valid_extraction(payment_timestamp="2026-06-20T10:38:00"),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
    )
    assert result.outcome == "exception"
    assert "no timezone" in result.reasons[0]


# --- Phase 4: settings-backed PayNow config -----------------------------------


def test_build_qr_uses_config_bill_number():
    cfg = _StubConfig()
    png = build_member_qr(fee_cents=2000, reference="BDM-1-X", config=cfg)
    result = zxingcpp.read_barcode(Image.open(io.BytesIO(png)))
    extra = paynow.parse_tlv(paynow.parse_tlv(result.text)["62"])
    assert extra["01"] == cfg.bill_number  # routing bill number swapped


def test_verify_uses_config_recipient_and_bill():
    cfg = _StubConfig()
    result = verify_extracted_payment(
        valid_extraction(recipient="New School Account", billing_id=cfg.bill_number),
        expected_fee_cents=5,
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
        config=cfg,
    )
    joined = " ".join(result.reasons)
    assert "Billing ID" not in joined
    assert "Recipient" not in joined
    assert result.passed
