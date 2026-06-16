"""Phase 0: the two test QRs that decide reference-code placement (PRD §13).

Variant A keeps the school's original bill number and carries our code in the
EMVCo reference-label subfield. Variant B replaces the bill number with our
code. The treasurer pays S$0.10 with each; what Flimax then shows decides the
strategy stored in settings.
"""

from decimal import Decimal

from clubbot.paynow import build_payload

SCHOOL_UEN = "200913519CSL5"
SCHOOL_MERCHANT_NAME = "SINGAPORE UNIVERSITY OF T"
SCHOOL_BILL_NUMBER = "200913519CSL5EIU616138169"

TEST_AMOUNT = Decimal("0.10")
VARIANT_A_REF = "BDMTEST01"
VARIANT_B_REF = "BDMTEST02"


def variant_a_payload() -> str:
    """School's bill number kept; our code rides in the reference label."""
    return build_payload(
        uen=SCHOOL_UEN,
        merchant_name=SCHOOL_MERCHANT_NAME,
        amount=TEST_AMOUNT,
        bill_number=SCHOOL_BILL_NUMBER,
        reference_label=VARIANT_A_REF,
    )


def variant_b_payload() -> str:
    """Our code replaces the school's bill number."""
    return build_payload(
        uen=SCHOOL_UEN,
        merchant_name=SCHOOL_MERCHANT_NAME,
        amount=TEST_AMOUNT,
        bill_number=VARIANT_B_REF,
    )
