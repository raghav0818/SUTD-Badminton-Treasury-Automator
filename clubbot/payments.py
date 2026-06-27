"""Payment QR construction and deterministic receipt verification."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from clubbot import paynow, qrgen

SCHOOL_UEN = "200913519CSL5"
SCHOOL_MERCHANT_NAME = "SINGAPORE UNIVERSITY OF T"
SCHOOL_BILL_NUMBER = "200913519CSL5EIU616138169"
SINGAPORE_TIME = timezone(timedelta(hours=8))


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


DEFAULT_RECIPIENT_MATCH = "SINGAPOREUNIVERSITYOF"


def build_member_qr(*, fee_cents: int, reference: str, config=None) -> bytes:
    """Build a locked-amount QR while preserving the school's routing Billing ID.

    `config` is any object exposing `.uen`, `.merchant_name`, `.bill_number`
    (e.g. ``paynow_config.PayNowConfig``). When omitted, the school's verified
    constants are used, so existing callers and tests are unaffected.
    """
    if fee_cents <= 0:
        raise ValueError("fee must be positive")
    payload = paynow.build_payload(
        uen=config.uen if config else SCHOOL_UEN,
        merchant_name=config.merchant_name if config else SCHOOL_MERCHANT_NAME,
        amount=Decimal(fee_cents) / Decimal(100),
        bill_number=config.bill_number if config else SCHOOL_BILL_NUMBER,
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
    config=None,
) -> VerificationResult:
    """Apply rules that, unlike the vision model, are deterministic and auditable.

    `config` (duck-typed `.recipient_match`, `.bill_number`) lets the treasurer
    re-point the bot at a new school account via `/settings` without code edits.
    When omitted, the verified school constants are used.
    """
    recipient_match = config.recipient_match if config else DEFAULT_RECIPIENT_MATCH
    expected_bill = config.bill_number if config else SCHOOL_BILL_NUMBER

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
    if recipient_match not in recipient:
        reasons.append("Recipient does not match the SUTD account.")

    if _normalise_text(extracted.billing_id) != _normalise_text(expected_bill):
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

    if not extracted.transaction_id:
        reasons.append("Transaction ID is missing.")
    elif duplicate_transaction:
        reasons.append("Transaction ID has already been submitted.")

    return VerificationResult(
        "exception" if reasons else "verified", tuple(reasons)
    )
