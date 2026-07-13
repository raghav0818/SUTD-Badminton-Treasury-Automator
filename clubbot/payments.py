"""Payment QR construction and deterministic receipt verification."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from clubbot import db, paynow, qrgen

SCHOOL_UEN = "200913519CSL5"
SCHOOL_MERCHANT_NAME = "SINGAPORE UNIVERSITY OF T"
SCHOOL_BILL_NUMBER = "200913519CSL5EIU616138169"
RECIPIENT_MATCH = "SINGAPORE UNIVERSITY OF"
SINGAPORE_TIME = db.SINGAPORE_TIME  # single source of truth for SGT


@dataclass(frozen=True)
class SchoolConfig:
    """QR routing + verification values; overridable via the settings table."""

    uen: str = SCHOOL_UEN
    merchant_name: str = SCHOOL_MERCHANT_NAME
    bill_number: str = SCHOOL_BILL_NUMBER
    recipient_match: str = RECIPIENT_MATCH


def school_config(conn: sqlite3.Connection) -> SchoolConfig:
    """Current school values: settings override, PRD defaults otherwise."""
    return SchoolConfig(
        uen=db.get_setting(conn, "school_uen") or SCHOOL_UEN,
        merchant_name=db.get_setting(conn, "school_merchant_name")
        or SCHOOL_MERCHANT_NAME,
        bill_number=db.get_setting(conn, "school_bill_number") or SCHOOL_BILL_NUMBER,
        recipient_match=db.get_setting(conn, "school_recipient_match")
        or RECIPIENT_MATCH,
    )


@dataclass(frozen=True)
class ExtractedPayment:
    readable: bool
    is_success_screen: bool
    amount_cents: int | None
    recipient: str | None
    billing_id: str | None
    payment_timestamp: str | None
    transaction_id: str | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class VerificationResult:
    outcome: str
    reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.outcome == "verified"


def build_member_qr(
    *, fee_cents: int, reference: str, school: SchoolConfig | None = None
) -> bytes:
    """Build a locked-amount QR while preserving the school's routing Billing ID."""
    if fee_cents <= 0:
        raise ValueError("fee must be positive")
    school = school or SchoolConfig()
    payload = paynow.build_payload(
        uen=school.uen,
        merchant_name=school.merchant_name,
        amount=Decimal(fee_cents) / Decimal(100),
        bill_number=school.bill_number,
        reference_label=reference,
    )
    return qrgen.render_png(payload)


def _normalise_text(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def normalise_transaction_id(value: str | None) -> str | None:
    normalised = _normalise_text(value)
    return normalised or None


def verify_extracted_payment(
    extracted: ExtractedPayment,
    *,
    expected_fee_cents: int,
    term_start: str,
    term_end: str,
    qr_issued_at: str,
    now: datetime | None = None,
    duplicate_transaction: bool = False,
    school: SchoolConfig | None = None,
) -> VerificationResult:
    """Apply rules that, unlike the vision model, are deterministic and auditable."""
    school = school or SchoolConfig()
    if not extracted.readable:
        return VerificationResult("retry", ("The screenshot is unreadable.",))
    if not extracted.is_success_screen:
        return VerificationResult(
            "retry", ("The image is not a completed-payment screen.",)
        )

    reasons: list[str] = []
    if extracted.amount_cents != expected_fee_cents:
        reasons.append(
            f"Amount is not the expected S${expected_fee_cents / 100:.2f}."
        )

    recipient = _normalise_text(extracted.recipient)
    if _normalise_text(school.recipient_match) not in recipient:
        reasons.append("Recipient does not match the SUTD account.")

    if _normalise_text(extracted.billing_id) != _normalise_text(school.bill_number):
        reasons.append("Billing ID does not match the club's DBS FLYMAX account.")

    try:
        paid_at = datetime.fromisoformat(extracted.payment_timestamp or "")
        issued_at = datetime.fromisoformat(qr_issued_at)
        if paid_at.tzinfo is None or issued_at.tzinfo is None:
            raise ValueError
        current = now or datetime.now(timezone.utc)
        paid_utc = paid_at.astimezone(timezone.utc)
        issued_utc = issued_at.astimezone(timezone.utc)
        paid_sg_date = paid_at.astimezone(SINGAPORE_TIME).date()
        if not (
            datetime.fromisoformat(term_start).date()
            <= paid_sg_date
            <= datetime.fromisoformat(term_end).date()
        ):
            reasons.append("Payment time is outside the valid term period.")
        # Bank screenshots commonly show only minute precision. Compare against
        # the beginning of the QR issue minute to avoid rejecting a real payment.
        issue_minute = issued_utc.replace(second=0, microsecond=0)
        if paid_utc < issue_minute:
            reasons.append("Payment was made before this payment QR was issued.")
        if paid_utc > current.astimezone(timezone.utc):
            reasons.append("Payment time is in the future.")
    except ValueError:
        reasons.append("Payment timestamp is missing, invalid, or has no timezone.")

    # Check the NORMALISED id: a reference of only punctuation/unicode
    # lookalikes must not slip past both this check and the dedup reservation
    # (which keys on the normalised value).
    if not normalise_transaction_id(extracted.transaction_id):
        reasons.append("Transaction ID is missing.")
    elif duplicate_transaction:
        reasons.append("Transaction ID has already been submitted.")

    return VerificationResult(
        "exception" if reasons else "verified", tuple(reasons)
    )
