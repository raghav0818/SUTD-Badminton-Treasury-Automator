"""Payment QR construction and deterministic receipt verification."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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
    # Every ID/reference on the receipt, verbatim: banks label the Billing ID
    # differently (UOB TMRW: "Reference no. (optional)"), so code matches it.
    reference_numbers: tuple[str, ...] = ()

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


def normalise_verification_text(value: str | None) -> str:
    """Canonical alphanumeric form used by deterministic receipt checks."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def normalise_transaction_id(value: str | None) -> str | None:
    normalised = normalise_verification_text(value)
    return normalised or None


def receipt_transaction_id(
    extracted: ExtractedPayment, school: SchoolConfig
) -> str | None:
    """The bank's own reference, or None if missing or misread as the school's ID.

    Every payer's receipt shows the same Billing ID/UEN; reserving one of them
    as a unique bank reference would reject every later payer as a duplicate.
    """
    txn = normalise_transaction_id(extracted.transaction_id)
    school_ids = {
        normalise_verification_text(school.bill_number),
        normalise_verification_text(school.uen),
    }
    return None if txn in school_ids else txn


def verify_extracted_payment(
    extracted: ExtractedPayment,
    *,
    valid_amounts: set[int],
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
    if extracted.amount_cents not in valid_amounts:
        expected = " or ".join(f"S${cents / 100:.2f}" for cents in sorted(valid_amounts))
        reasons.append(f"Amount is not the expected {expected}.")

    recipient = normalise_verification_text(extracted.recipient)
    expected_recipient = normalise_verification_text(school.recipient_match)
    if not expected_recipient or expected_recipient not in recipient:
        reasons.append("Recipient does not match the SUTD account.")

    expected_bill = normalise_verification_text(school.bill_number)
    seen_ids = {
        normalise_verification_text(value)
        for value in (extracted.billing_id, *(extracted.reference_numbers or ()))
    }
    if not expected_bill or expected_bill not in seen_ids:
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

    # A missing transaction ID does NOT block auto-verify: most banks only
    # show it behind an "expand details" tap payers routinely skip, so
    # requiring it would send most legitimate payments to manual review.
    # /reconcile's bank-line matching (amount + time + a shared name word
    # against the treasurer's own FLYMAX list) is the real backstop for
    # these. A transaction ID that IS present and already used by another
    # payment still blocks here — that's unambiguous reuse, no need to wait
    # for reconcile.
    if receipt_transaction_id(extracted, school) and duplicate_transaction:
        reasons.append("Transaction ID has already been submitted.")

    return VerificationResult(
        "exception" if reasons else "verified", tuple(reasons)
    )


# --- Bank reconciliation -----------------------------------------------------------
# A receipt can be faked; the school's own FLYMAX list cannot (the treasurer
# screenshots it). Each verified payment must find its own bank line.

MATCH_WINDOW = timedelta(minutes=15)  # receipt time vs FLYMAX time
DATE_ONLY_WINDOW = timedelta(hours=12)  # a date-only line sits at noon: same SGT day
NAME_NOISE = {"MR", "MS", "MRS", "MDM", "DR", "MISS"}


@dataclass(frozen=True)
class BankLine:
    amount_cents: int | None
    paid_at: str | None
    payer_name: str | None
    transaction_id: str | None


@dataclass(frozen=True)
class ReconcileResult:
    confirmed: tuple[int, ...]  # payment ids newly backed by a bank line
    # (payment id, bank line) where only the payer name differs
    name_differs: tuple[tuple[int, BankLine], ...]
    # a bank line fits this payment AND another one: nobody auto-confirmed
    ambiguous: tuple[int, ...]
    missing: tuple[int, ...]  # inside the covered period, no bank line at all
    waiting: int  # unconfirmed, outside the covered period: next time
    stray_lines: tuple[BankLine, ...]  # bank money no member's receipt claims
    unreadable_lines: int
    covered: tuple[datetime, datetime] | None
    # already-confirmed payments now contested by another receipt: un-confirm
    suspended: tuple[int, ...] = ()
    # (payment id, FLYMAX txn ID) for every match: that transfer is theirs for good
    claimed_lines: tuple[tuple[int, str], ...] = ()
    # rows read without their TXN ID (FLYMAX always shows one, so a misread):
    # they can't be tied to one transfer for good, so they confirm nobody
    no_txn_lines: tuple[BankLine, ...] = ()


def _line_time(line: BankLine) -> tuple[datetime, timedelta] | None:
    try:
        when = datetime.fromisoformat(line.paid_at or "")
    except ValueError:
        return None
    if len(line.paid_at) == 10:  # "YYYY-MM-DD"
        return when.replace(hour=12, tzinfo=SINGAPORE_TIME), DATE_ONLY_WINDOW
    if when.tzinfo is None:
        when = when.replace(tzinfo=SINGAPORE_TIME)  # FLYMAX shows SGT
    return when, MATCH_WINDOW


def _name_tokens(name: str | None) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Z]+", (name or "").upper())
        if len(token) >= 2 and token not in NAME_NOISE
    }


def _payment_ids(payment, school_ids: set[str]) -> set[str]:
    """The member's receipt references, minus the Billing ID/UEN every receipt shares."""
    ids = {payment["bank_txn_id"] or ""}
    try:
        extracted = json.loads(payment["extracted_json"] or "{}")
    except ValueError:
        extracted = {}
    ids.add(extracted.get("transaction_id") or "")
    ids.update(extracted.get("reference_numbers") or ())
    return {normalise_verification_text(value) for value in ids} - {""} - school_ids


@dataclass
class _Row:
    line: BankLine
    at: datetime
    window: timedelta
    txn: str
    name: str

    def same_transfer(self, other: "_Row") -> bool:
        """Overlapping screenshots repeat rows, and Gemini may read the copy a
        little differently (no ID, a truncated name). Collapsing too much only
        flags an honest payer; keeping a copy could let two payments share it."""
        if self.txn and other.txn:
            return self.txn == other.txn
        return (
            self.line.amount_cents == other.line.amount_cents
            and abs(self.at - other.at) <= DUPLICATE_GAP
            # "ALICE" is a truncated read of "ALICE TAN"; "" fits anything
            and (self.name.startswith(other.name) or other.name.startswith(self.name))
        )


DUPLICATE_GAP = timedelta(minutes=1)


def reconcile(
    payments,
    lines: list[BankLine],
    school: SchoolConfig | None = None,
    owners: dict[str, int] | None = None,
) -> ReconcileResult:
    """Match receipt-verified payments to FLYMAX lines; one line backs one payment.

    `payments` are db.list_reconcile_payments rows. `owners` maps a FLYMAX txn
    ID to the payment (any term, any status) it already confirmed: that
    transfer can never back anybody else. A payment is confirmed only when the
    evidence is unambiguous; a line that fits two payments (including an
    already-confirmed one) confirms neither, and both go to the treasurer.
    """
    school = school or SchoolConfig()
    owners = owners or {}
    school_ids = {
        normalise_verification_text(school.bill_number),
        normalise_verification_text(school.uen),
    } - {""}

    rows: list[_Row] = []
    unreadable = 0
    for line in lines:
        timed = _line_time(line)
        if timed is None or not line.amount_cents:
            unreadable += 1
            continue
        txn = normalise_verification_text(line.transaction_id)
        row = _Row(
            line, *timed, "" if txn in school_ids else txn,
            normalise_verification_text(line.payer_name),
        )
        twin = next((r for r in rows if r.same_transfer(row)), None)
        if twin is None:
            rows.append(row)
            continue
        twin.txn = twin.txn or row.txn
        if len(row.name) > len(twin.name):  # keep the untruncated name
            twin.line, twin.name = row.line, row.name

    # Collapsing copies first lets a re-read without its ID borrow it. A row
    # still without one is only used for the covered period: confirming from
    # it would leave no permanent claim, so its transfer could be reused.
    timed_rows = rows
    blind = [r.line for r in rows if not r.txn]
    rows = [r for r in rows if r.txn]

    by_id = {p["id"]: p for p in payments}
    ids = {p["id"]: _payment_ids(p, school_ids) for p in payments}

    def paid_at(payment) -> datetime | None:
        try:
            when = datetime.fromisoformat(payment["payment_timestamp"] or "")
        except ValueError:
            return None
        return when if when.tzinfo else None

    def fits(payment, i: int, *, same_name: bool) -> bool:
        row, when = rows[i], paid_at(payment)
        return (
            when is not None
            and row.line.amount_cents == payment["amount_cents"]
            and abs(when - row.at) <= row.window
            and (
                not same_name
                or bool(_name_tokens(payment["full_name"]) & _name_tokens(row.line.payer_name))
            )
        )

    def id_claims(payment, i: int) -> bool:
        # The ID must agree on time too: an old transfer's ID pasted into a new
        # receipt is not that transfer. No timestamp (rare, treasurer-approved):
        # the ID alone, as before.
        row, when = rows[i], paid_at(payment)
        return (
            bool(row.txn)
            and row.txn in ids[payment["id"]]
            and row.line.amount_cents == payment["amount_cents"]
            and (when is None or abs(when - row.at) <= row.window)
        )

    used: set[int] = set()
    matched: dict[int, int] = {}  # payment id -> row index backing it this run
    # A receipt whose IDs match 2+ transfers (owned or not) can't be trusted.
    contested: set[int] = {
        pid for pid, p in by_id.items()
        if sum(id_claims(p, i) for i in range(len(rows))) > 1
    }

    # Pass 0: a transfer that already confirmed somebody stays theirs. It
    # re-backs its owner only if the owner's current receipt still fits it;
    # any other receipt that fits it contests it (and the owner with it). As in
    # passes 1-2, an ID claim outranks name + time.
    for i, row in enumerate(rows):
        if row.txn not in owners:
            continue
        used.add(i)
        owner = owners[row.txn]
        claimants = [pid for pid, p in by_id.items() if id_claims(p, i)] or [
            pid for pid, p in by_id.items() if fits(p, i, same_name=True)
        ]
        if claimants == [owner] and owner not in contested:
            matched[owner] = i
        elif claimants:
            contested.update(claimants)
            if owner in by_id:
                contested.add(owner)

    # Pass 1: the line's txn ID is on exactly one receipt, and that receipt's
    # IDs match exactly one line (one receipt can't consume two transfers).
    claims = {
        pid: [i for i in range(len(rows)) if i not in used and id_claims(p, i)]
        for pid, p in by_id.items()
        if pid not in matched and pid not in contested
    }
    contested.update(pid for pid, claimed in claims.items() if len(claimed) > 1)
    for i in range(len(rows)):
        claimants = [pid for pid, claimed in claims.items() if i in claimed]
        if len(claimants) == 1 and claimants[0] not in contested:
            used.add(i)
            matched[claimants[0]] = i

    # Pass 2: same amount, close in time, a shared name word, and nobody else
    # (not even an already-confirmed payment) fits that line.
    rest = [p for p in payments if p["id"] not in matched and p["id"] not in contested]
    candidates = {
        i: [p["id"] for p in rest if fits(p, i, same_name=True)]
        for i in range(len(rows))
        if i not in used
    }
    for payment in sorted(rest, key=lambda p: p["audit_confirmed_at"] is None):
        for i, fitting in candidates.items():
            if i not in used and fitting == [payment["id"]]:
                used.add(i)
                matched[payment["id"]] = i
                break
    contested.update(
        pid for i, fitting in candidates.items() if i not in used for pid in fitting
    )

    covered = None
    if timed_rows:
        start = min(timed_rows, key=lambda r: r.at)
        end = max(timed_rows, key=lambda r: r.at)
        covered = (start.at + start.window, end.at - end.window)

    def in_covered(payment) -> bool:
        when = paid_at(payment)
        return bool(covered and when and covered[0] < when < covered[1])

    name_differs, ambiguous, suspended, missing, waiting = [], [], [], [], 0
    for payment in payments:
        pid = payment["id"]
        if pid in matched:
            continue
        if pid in contested:
            ambiguous.append(pid)
            if payment["audit_confirmed_at"]:
                suspended.append(pid)  # it may be the fake that got in first
            continue
        if payment["audit_confirmed_at"]:
            continue
        # Pass 3 (report only): amount and time fit but the name doesn't, e.g.
        # a parent paid. Never confirms anything, so the line isn't consumed.
        near = [
            i for i in range(len(rows))
            if i not in used and fits(payment, i, same_name=False)
        ]
        if near:
            when = paid_at(payment)
            name_differs.append(
                (pid, rows[min(near, key=lambda i: abs(when - rows[i].at))].line)
            )
        elif in_covered(payment):
            missing.append(pid)
        else:
            waiting += 1

    newly = tuple(
        p["id"] for p in payments if p["id"] in matched and not p["audit_confirmed_at"]
    )
    return ReconcileResult(
        confirmed=newly,
        name_differs=tuple(name_differs),
        ambiguous=tuple(ambiguous),
        missing=tuple(missing),
        waiting=waiting,
        stray_lines=tuple(r.line for i, r in enumerate(rows) if i not in used),
        unreadable_lines=unreadable,
        covered=covered,
        suspended=tuple(suspended),
        claimed_lines=tuple((pid, rows[i].txn) for pid, i in matched.items()),
        no_txn_lines=tuple(blind),
    )
