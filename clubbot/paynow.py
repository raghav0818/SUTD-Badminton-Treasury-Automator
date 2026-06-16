"""PayNow / EMVCo QR payload building and parsing.

The school's decoded QR (PRD §4) is the golden test vector: rebuilding it from
its parts must reproduce the payload byte-identically.
"""

from __future__ import annotations

from decimal import Decimal

# EMVCo root tags
TAG_PAYLOAD_FORMAT = "00"
TAG_POI_METHOD = "01"  # "11" = static QR, "12" = dynamic QR
TAG_MERCHANT_ACCOUNT = "26"
TAG_MCC = "52"
TAG_CURRENCY = "53"
TAG_AMOUNT = "54"
TAG_COUNTRY = "58"
TAG_MERCHANT_NAME = "59"
TAG_MERCHANT_CITY = "60"
TAG_ADDITIONAL_DATA = "62"
TAG_CRC = "63"

# Tag 26 sub-tags (PayNow)
SUB_DOMAIN = "00"           # always "SG.PAYNOW"
SUB_PROXY_TYPE = "01"       # "0" = mobile, "2" = UEN
SUB_PROXY_VALUE = "02"
SUB_AMOUNT_EDITABLE = "03"  # "1" = payer may edit amount, "0" = locked
SUB_EXPIRY = "04"           # YYYYMMDD

# Tag 62 sub-tags (EMVCo additional data)
SUB_BILL_NUMBER = "01"
SUB_REFERENCE_LABEL = "05"

CURRENCY_SGD = "702"


def crc16_ccitt(data: str) -> str:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) as 4 uppercase hex chars."""
    crc = 0xFFFF
    for byte in data.encode("ascii"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def tlv(tag: str, value: str) -> str:
    if not value:
        raise ValueError(f"empty value for tag {tag}")
    if len(value) > 99:
        raise ValueError(f"value too long for tag {tag}: {len(value)} chars")
    return f"{tag}{len(value):02d}{value}"


def parse_tlv(data: str) -> dict[str, str]:
    """Parse one level of TLV data into {tag: value}."""
    out: dict[str, str] = {}
    i = 0
    while i < len(data):
        tag = data[i : i + 2]
        length = int(data[i + 2 : i + 4])
        value = data[i + 4 : i + 4 + length]
        if len(value) != length:
            raise ValueError(f"truncated TLV at tag {tag}")
        out[tag] = value
        i += 4 + length
    return out


def verify_crc(payload: str) -> bool:
    """True if the payload's trailing CRC matches its content."""
    if len(payload) < 8 or payload[-8:-4] != TAG_CRC + "04":
        return False
    return crc16_ccitt(payload[:-4]) == payload[-4:]


def build_payload(
    *,
    uen: str,
    merchant_name: str,
    amount: Decimal | None = None,
    editable_amount: bool = False,
    expiry: str = "29991231",
    bill_number: str | None = None,
    reference_label: str | None = None,
    merchant_city: str = "SG",
) -> str:
    """Build a PayNow EMVCo payload string.

    amount=None with editable_amount=True reproduces a static QR like the
    school's; a fixed amount produces a dynamic QR the payer cannot edit.
    """
    if amount is None and not editable_amount:
        raise ValueError("a QR with no amount must have editable_amount=True")
    if amount is not None and editable_amount:
        raise ValueError("a fixed-amount QR must not be editable")

    merchant_account = (
        tlv(SUB_DOMAIN, "SG.PAYNOW")
        + tlv(SUB_PROXY_TYPE, "2")
        + tlv(SUB_PROXY_VALUE, uen)
        + tlv(SUB_AMOUNT_EDITABLE, "1" if editable_amount else "0")
        + tlv(SUB_EXPIRY, expiry)
    )

    parts = [
        tlv(TAG_PAYLOAD_FORMAT, "01"),
        tlv(TAG_POI_METHOD, "11" if amount is None else "12"),
        tlv(TAG_MERCHANT_ACCOUNT, merchant_account),
        tlv(TAG_MCC, "0000"),
        tlv(TAG_CURRENCY, CURRENCY_SGD),
    ]
    if amount is not None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        parts.append(tlv(TAG_AMOUNT, f"{amount:.2f}"))
    parts.append(tlv(TAG_COUNTRY, "SG"))
    parts.append(tlv(TAG_MERCHANT_NAME, merchant_name))
    parts.append(tlv(TAG_MERCHANT_CITY, merchant_city))

    additional = ""
    if bill_number:
        additional += tlv(SUB_BILL_NUMBER, bill_number)
    if reference_label:
        additional += tlv(SUB_REFERENCE_LABEL, reference_label)
    if additional:
        parts.append(tlv(TAG_ADDITIONAL_DATA, additional))

    body = "".join(parts) + TAG_CRC + "04"
    return body + crc16_ccitt(body)
