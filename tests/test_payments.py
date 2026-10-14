import io
from datetime import datetime, timezone

import zxingcpp
from PIL import Image

from clubbot import db, paynow
from clubbot.payments import (
    SCHOOL_BILL_NUMBER,
    SCHOOL_UEN,
    ExtractedPayment,
    SchoolConfig,
    build_member_qr,
    receipt_transaction_id,
    school_config,
    verify_extracted_payment,
)


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
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
    )
    assert result.passed
    assert result.reasons == ()


def test_either_valid_amount_passes_and_others_fail():
    def check(amount):
        return verify_extracted_payment(
            valid_extraction(amount_cents=amount),
            valid_amounts={2000, 3500},
            term_start="2026-06-01",
            term_end="2026-06-30",
            qr_issued_at="2026-06-20T02:30:00+00:00",
            now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
        )

    assert check(2000).passed
    assert check(3500).passed
    wrong = check(2500)
    assert wrong.outcome == "exception"
    assert wrong.reasons == ("Amount is not the expected S$20.00 or S$35.00.",)
    assert check(None).outcome == "exception"


def test_unreadable_or_incomplete_screen_requests_retry():
    unreadable = verify_extracted_payment(
        valid_extraction(readable=False),
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
    )
    incomplete = verify_extracted_payment(
        valid_extraction(is_success_screen=False),
        valid_amounts={5},
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
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
    )
    assert result.outcome == "exception"
    assert len(result.reasons) == 5


def test_duplicate_transaction_goes_to_exception():
    result = verify_extracted_payment(
        valid_extraction(),
        valid_amounts={5},
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
        valid_amounts={5},
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
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
    )
    assert result.outcome == "exception"
    assert "no timezone" in result.reasons[0]


def test_transaction_id_that_normalises_to_empty_does_not_block_verification():
    # Punctuation-only or unicode-lookalike references normalise to nothing,
    # same as a genuinely missing ID: that's fine now (/reconcile is the
    # backstop), as long as it also can't be reserved for dedup elsewhere.
    extracted = valid_extraction(transaction_id="ＴＸ－１２３")  # fullwidth: normalises to ""
    assert receipt_transaction_id(extracted, SchoolConfig()) is None
    result = verify_extracted_payment(
        extracted,
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
    )
    assert result.passed


def test_verification_fails_closed_for_empty_normalised_school_checks():
    result = verify_extracted_payment(
        valid_extraction(recipient=None, billing_id=None),
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
        school=SchoolConfig(recipient_match="!!!", bill_number="!!!"),
    )
    assert result.outcome == "exception"
    assert any("Recipient" in reason for reason in result.reasons)
    assert any("Billing ID" in reason for reason in result.reasons)


def _check(extracted):
    return verify_extracted_payment(
        extracted,
        valid_amounts={20, 30},
        term_start="2026-10-07",
        term_end="2026-10-20",
        qr_issued_at="2026-10-10T11:12:24+00:00",
        now=datetime(2026, 10, 11, 11, 0, tzinfo=timezone.utc),
    )


# Gemini's real output for a live UOB TMRW receipt (2026-10-11): the UEN came
# back as billing_id; the full Billing ID sat under "Reference no. (optional)".
UOB_TMRW = dict(
    amount_cents=20,
    recipient="SINGAPORE UNIVERSITY OF TECHNOLOGY AND DESIGN",
    billing_id=SCHOOL_UEN,
    payment_timestamp="2026-10-11T18:28:00+08:00",
    transaction_id="2610112184655201",
    reference_numbers=(SCHOOL_UEN, SCHOOL_BILL_NUMBER, "2610112184655201"),
)


def test_billing_id_found_among_reference_numbers_passes():
    assert _check(valid_extraction(**UOB_TMRW)).passed


def test_billing_id_matching_ignores_spacing_and_case():
    spaced = "200913519 csl5 eiu6161-38169"
    assert _check(valid_extraction(**{**UOB_TMRW, "reference_numbers": (spaced,)})).passed


def test_uen_alone_or_truncated_billing_id_still_fails():
    for refs in ((SCHOOL_UEN,), (SCHOOL_BILL_NUMBER[:-1],), (SCHOOL_BILL_NUMBER + "0",), ()):
        result = _check(valid_extraction(**{**UOB_TMRW, "reference_numbers": refs}))
        assert result.outcome == "exception"
        assert result.reasons == ("Billing ID does not match the club's DBS FLYMAX account.",)


def test_null_reference_numbers_falls_back_to_billing_id():
    assert _check(
        valid_extraction(**{**UOB_TMRW, "billing_id": SCHOOL_BILL_NUMBER, "reference_numbers": None})
    ).passed


def test_school_id_misread_as_transaction_id_is_treated_as_missing():
    for misread in (SCHOOL_BILL_NUMBER, SCHOOL_UEN, SCHOOL_BILL_NUMBER.lower()):
        extracted = valid_extraction(**{**UOB_TMRW, "transaction_id": misread})
        assert receipt_transaction_id(extracted, SchoolConfig()) is None
        # Treated as missing, not a mismatch: still auto-verifies.
        assert _check(extracted).passed
    assert receipt_transaction_id(valid_extraction(**UOB_TMRW), SchoolConfig()) == "2610112184655201"


def test_missing_transaction_id_does_not_block_auto_verify():
    # The common real-world case: a bank's collapsed success screen shows no
    # transaction/reference number at all (only the school's Billing ID).
    # Everything else checking out is enough; /reconcile covers the rest.
    extracted = valid_extraction(transaction_id=None, reference_numbers=())
    result = verify_extracted_payment(
        extracted,
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
    )
    assert result.passed


def test_school_config_defaults_and_settings_override():
    conn = db.connect(":memory:")
    assert school_config(conn).uen == SCHOOL_UEN
    assert school_config(conn).bill_number == SCHOOL_BILL_NUMBER

    db.set_setting(conn, "school_bill_number", "NEWBILL999")
    school = school_config(conn)
    assert school.bill_number == "NEWBILL999"
    assert school.uen == SCHOOL_UEN  # untouched keys keep their defaults

    # Verification follows the override: the old default billing id now fails.
    result = verify_extracted_payment(
        valid_extraction(),
        valid_amounts={5},
        term_start="2026-06-01",
        term_end="2026-06-30",
        qr_issued_at="2026-06-20T02:30:00+00:00",
        now=datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc),
        school=school,
    )
    assert result.outcome == "exception"
    assert any("Billing ID" in reason for reason in result.reasons)


def test_member_qr_uses_school_config_override():
    conn = db.connect(":memory:")
    db.set_setting(conn, "school_bill_number", "NEWBILL999")
    png = build_member_qr(
        fee_cents=5, reference="BDM-1-ABC123", school=school_config(conn)
    )
    result = zxingcpp.read_barcode(Image.open(io.BytesIO(png)))
    extra = paynow.parse_tlv(paynow.parse_tlv(result.text)["62"])
    assert extra["01"] == "NEWBILL999"
