# Phase 0 Tooling + Phase 1 Core Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the PayNow QR generator (with the two Phase 0 test QRs the treasurer must pay) and the Phase 1 core Telegram bot: member registration, SQLite schema, `/status`, `/help`, and treasurer bootstrap.

**Architecture:** One Python package `clubbot/` with pure modules (`paynow` EMVCo payload builder, `qrgen` PNG renderer, `db` SQLite layer, `validation`) and a thin Telegram layer (`bot.py` handlers, `__main__.py` entry point). Handlers get the DB connection via `application.bot_data["db"]`, so all logic is unit-testable with an in-memory SQLite DB and mocked updates. Phases 2–5 (payment engine, lifecycle, Sheets, deploy) are **out of scope** — Phase 2 is blocked until the Phase 0 test-payment outcome is recorded in `MEMORY.md` (PRD §13).

**Tech Stack:** Python 3.12+ · python-telegram-bot v21+ (long-polling) · SQLite (stdlib `sqlite3`) · `qrcode[pil]` · `python-dotenv` · pytest · `zxing-cpp` (test-only QR decode).

**Reference vector (PRD §4):** the school's decoded QR payload — every byte of it — is the golden test for the payload builder:

```
00020101021126520009SG.PAYNOW010120213200913519CSL5030110408299912315204000053037025802SG5925SINGAPORE UNIVERSITY OF T6002SG62290125200913519CSL5EIU6161381696304432B
```

Parsed: tag 00=`01`, tag 01=`11` (static), tag 26 = {00=`SG.PAYNOW`, 01=`2` (UEN proxy), 02=`200913519CSL5`, 03=`1` (amount editable), 04=`29991231`}, 52=`0000`, 53=`702` (SGD), 58=`SG`, 59=`SINGAPORE UNIVERSITY OF T`, 60=`SG`, 62={01=`200913519CSL5EIU616138169` (bill number)}, 63=`432B` (CRC-16/CCITT-FALSE).

---

## File structure

```
.gitignore                      — env/venv/db/artifacts
requirements.txt                — runtime + test deps (one file; solo project)
pytest.ini                      — pytest config
.env.example                    — template for secrets
README.md                       — treasurer-facing setup/run instructions
clubbot/
  __init__.py                   — empty package marker
  paynow.py                     — EMVCo TLV build/parse + CRC-16 (pure, no I/O)
  qrgen.py                      — payload string -> PNG bytes
  phase0.py                     — the two Phase 0 test-QR payloads (PRD §13)
  validation.py                 — name / SUTD-ID input validators (pure)
  db.py                         — schema (PRD §8) + queries
  config.py                     — .env loading -> Config dataclass
  bot.py                        — PTB handlers + build_application()
  __main__.py                   — python -m clubbot entry point
scripts/
  make_phase0_qrs.py            — writes phase0_qrs/variant_A.png + variant_B.png
tests/
  test_paynow.py
  test_qrgen.py
  test_phase0.py
  test_validation.py
  test_db.py
  test_bot.py
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `.gitignore`, `requirements.txt`, `pytest.ini`, `clubbot/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create a work branch**

```powershell
git checkout -b phase0-phase1-core-bot
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
*.db
phase0_qrs/
```

- [ ] **Step 3: Write `requirements.txt`**

```
python-telegram-bot>=21.0
qrcode[pil]>=7.4
python-dotenv>=1.0
pytest>=8.0
zxing-cpp>=2.2
```

- [ ] **Step 4: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 5: Create empty `clubbot/__init__.py` and `tests/__init__.py`**

- [ ] **Step 6: Create venv and install deps**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Expected: all packages install without error. (If `zxing-cpp` has no wheel for this platform, remove it from requirements and skip the decode-back test in Task 3 — the magic-bytes test still covers rendering.)

- [ ] **Step 7: Verify pytest runs**

```powershell
.venv\Scripts\python -m pytest
```

Expected: `no tests ran` (exit code 5 is fine).

- [ ] **Step 8: Commit**

```powershell
git add .gitignore requirements.txt pytest.ini clubbot/__init__.py tests/__init__.py
git commit -m "chore: project scaffolding (deps, pytest, package skeleton)"
```

---

### Task 2: `paynow.py` — EMVCo payload builder, parser, CRC

**Files:**
- Create: `clubbot/paynow.py`
- Test: `tests/test_paynow.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_paynow.py` (complete file):

```python
from decimal import Decimal

import pytest

from clubbot import paynow

SCHOOL_PAYLOAD = "00020101021126520009SG.PAYNOW010120213200913519CSL5030110408299912315204000053037025802SG5925SINGAPORE UNIVERSITY OF T6002SG62290125200913519CSL5EIU6161381696304432B"


def test_crc_of_school_qr():
    assert paynow.crc16_ccitt(SCHOOL_PAYLOAD[:-4]) == "432B"


def test_verify_crc():
    assert paynow.verify_crc(SCHOOL_PAYLOAD)
    assert not paynow.verify_crc(SCHOOL_PAYLOAD[:-1] + "C")


def test_school_qr_reencodes_byte_identically():
    rebuilt = paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        editable_amount=True,
        bill_number="200913519CSL5EIU616138169",
    )
    assert rebuilt == SCHOOL_PAYLOAD


def test_parse_tlv_school_qr():
    root = paynow.parse_tlv(SCHOOL_PAYLOAD)
    assert root["00"] == "01"
    assert root["01"] == "11"
    account = paynow.parse_tlv(root["26"])
    assert account["00"] == "SG.PAYNOW"
    assert account["01"] == "2"
    assert account["02"] == "200913519CSL5"
    assert account["03"] == "1"
    assert account["04"] == "29991231"
    assert root["59"] == "SINGAPORE UNIVERSITY OF T"
    assert paynow.parse_tlv(root["62"])["01"] == "200913519CSL5EIU616138169"


def test_dynamic_payload_with_fixed_amount():
    payload = paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        amount=Decimal("12.50"),
        bill_number="BDM-T5-047",
    )
    assert paynow.verify_crc(payload)
    root = paynow.parse_tlv(payload)
    assert root["01"] == "12"  # dynamic QR
    assert root["54"] == "12.50"
    account = paynow.parse_tlv(root["26"])
    assert account["03"] == "0"  # payer cannot edit the amount
    assert paynow.parse_tlv(root["62"])["01"] == "BDM-T5-047"


def test_reference_label_subfield():
    payload = paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        amount=Decimal("0.10"),
        bill_number="200913519CSL5EIU616138169",
        reference_label="BDMTEST01",
    )
    extra = paynow.parse_tlv(paynow.parse_tlv(payload)["62"])
    assert extra["01"] == "200913519CSL5EIU616138169"
    assert extra["05"] == "BDMTEST01"


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        paynow.build_payload(uen="X", merchant_name="Y")  # no amount and not editable
    with pytest.raises(ValueError):
        paynow.build_payload(
            uen="X", merchant_name="Y", amount=Decimal("1.00"), editable_amount=True
        )
    with pytest.raises(ValueError):
        paynow.build_payload(uen="X", merchant_name="Y", amount=Decimal("0.00"))
    with pytest.raises(ValueError):
        paynow.tlv("01", "")
    with pytest.raises(ValueError):
        paynow.tlv("01", "x" * 100)
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_paynow.py -v
```

Expected: FAIL — `module 'clubbot' has no attribute 'paynow'` / import error.

- [ ] **Step 3: Write the implementation**

`clubbot/paynow.py` (complete file):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
.venv\Scripts\python -m pytest tests/test_paynow.py -v
```

Expected: all PASS (byte-identical re-encode proves field order, lengths, and CRC are right).

- [ ] **Step 5: Commit**

```powershell
git add clubbot/paynow.py tests/test_paynow.py
git commit -m "feat: EMVCo PayNow payload builder/parser with CRC-16, verified against school QR"
```

---

### Task 3: `qrgen.py` — render payload to PNG

**Files:**
- Create: `clubbot/qrgen.py`
- Test: `tests/test_qrgen.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_qrgen.py` (complete file):

```python
import io

import zxingcpp
from PIL import Image

from clubbot import paynow, qrgen


def _school_like_payload() -> str:
    return paynow.build_payload(
        uen="200913519CSL5",
        merchant_name="SINGAPORE UNIVERSITY OF T",
        editable_amount=True,
        bill_number="200913519CSL5EIU616138169",
    )


def test_render_png_magic_bytes():
    png = qrgen.render_png(_school_like_payload())
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_rendered_qr_decodes_back_to_payload():
    payload = _school_like_payload()
    img = Image.open(io.BytesIO(qrgen.render_png(payload)))
    results = zxingcpp.read_barcodes(img)
    assert len(results) == 1
    assert results[0].text == payload
```

(If `zxing-cpp` could not be installed in Task 1, drop the second test and the `zxingcpp` import.)

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_qrgen.py -v
```

Expected: FAIL — `cannot import name 'qrgen'`.

- [ ] **Step 3: Write the implementation**

`clubbot/qrgen.py` (complete file):

```python
"""Render an EMVCo payload string to a PNG QR image."""

import io

import qrcode


def render_png(payload: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
.venv\Scripts\python -m pytest tests/test_qrgen.py -v
```

Expected: PASS (round-trip: build → render → scan → identical payload).

- [ ] **Step 5: Commit**

```powershell
git add clubbot/qrgen.py tests/test_qrgen.py
git commit -m "feat: render PayNow payloads to PNG QR codes"
```

---

### Task 4: `phase0.py` + script — the two test QRs (PRD §13)

**Files:**
- Create: `clubbot/phase0.py`, `scripts/make_phase0_qrs.py`
- Test: `tests/test_phase0.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_phase0.py` (complete file):

```python
from clubbot import paynow, phase0


def test_variant_a_keeps_school_bill_number_and_adds_reference_label():
    payload = phase0.variant_a_payload()
    assert paynow.verify_crc(payload)
    root = paynow.parse_tlv(payload)
    extra = paynow.parse_tlv(root["62"])
    assert extra["01"] == phase0.SCHOOL_BILL_NUMBER
    assert extra["05"] == "BDMTEST01"
    assert root["54"] == "0.10"
    assert paynow.parse_tlv(root["26"])["03"] == "0"  # amount locked


def test_variant_b_replaces_bill_number_with_our_code():
    payload = phase0.variant_b_payload()
    assert paynow.verify_crc(payload)
    root = paynow.parse_tlv(payload)
    extra = paynow.parse_tlv(root["62"])
    assert extra["01"] == "BDMTEST02"
    assert "05" not in extra
    assert root["54"] == "0.10"
    assert paynow.parse_tlv(root["26"])["02"] == phase0.SCHOOL_UEN
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_phase0.py -v
```

Expected: FAIL — no module `phase0`.

- [ ] **Step 3: Write the implementation**

`clubbot/phase0.py` (complete file):

```python
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
```

`scripts/make_phase0_qrs.py` (complete file):

```python
"""Generate the two Phase 0 test QRs (PRD §13).

Run:  python scripts/make_phase0_qrs.py
Then pay S$0.10 with each QR from your own bank app and check the DBS Flimax
transaction history — see the printed instructions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clubbot import phase0, qrgen  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "phase0_qrs"

INSTRUCTIONS = """
Phase 0 test (PRD §13) — what to do now:

1. Open each PNG in {out_dir}
   and pay S$0.10 with your personal bank app:
     variant_A.png — keeps the school's bill number; our code BDMTEST01 rides
                     in the EMVCo "reference label" field
     variant_B.png — our code BDMTEST02 REPLACES the school's bill number
2. Wait for both to clear, then check the DBS Flimax transaction history:
     a. Did BOTH payments arrive in the club account?
     b. What text shows for each payment (which code, if any)?
     c. Did variant B still get allocated to the club account?
3. Report the answers back — the outcome is recorded in MEMORY.md and decides
   how member reference codes are placed in real payment QRs (Phase 2).
"""


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for name, payload in [
        ("variant_A", phase0.variant_a_payload()),
        ("variant_B", phase0.variant_b_payload()),
    ]:
        path = OUT_DIR / f"{name}.png"
        path.write_bytes(qrgen.render_png(payload))
        print(f"wrote {path}")
    print(INSTRUCTIONS.format(out_dir=OUT_DIR))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, then the script**

```powershell
.venv\Scripts\python -m pytest tests/test_phase0.py -v
.venv\Scripts\python scripts/make_phase0_qrs.py
```

Expected: tests PASS; script prints two `wrote ...phase0_qrs\variant_X.png` lines plus instructions; both PNG files exist.

- [ ] **Step 5: Commit**

```powershell
git add clubbot/phase0.py scripts/make_phase0_qrs.py tests/test_phase0.py
git commit -m "feat: Phase 0 test QR variants A/B and generator script"
```

---

### Task 5: `db.py` — SQLite schema and queries

**Files:**
- Create: `clubbot/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_db.py` (complete file):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_db.py -v
```

Expected: FAIL — no module `db`.

- [ ] **Step 3: Write the implementation**

`clubbot/db.py` (complete file):

```python
"""SQLite schema (PRD §8) and queries."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    telegram_user_id INTEGER PRIMARY KEY,
    full_name        TEXT    NOT NULL,
    sutd_id          TEXT    NOT NULL UNIQUE,
    username         TEXT,
    joined_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    active           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS terms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    fee_cents  INTEGER NOT NULL,
    start_date TEXT    NOT NULL,
    end_date   TEXT    NOT NULL,
    created_by INTEGER,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id          INTEGER NOT NULL REFERENCES members(telegram_user_id),
    term_id            INTEGER NOT NULL REFERENCES terms(id),
    ref_code           TEXT    NOT NULL UNIQUE,
    status             TEXT    NOT NULL CHECK (status IN
        ('awaiting_payment','pending_verification','verified',
         'exception','rejected','revoked')),
    amount_cents       INTEGER,
    screenshot_file_id TEXT,
    extracted_json     TEXT,
    bank_txn_id        TEXT    UNIQUE,
    image_hash         TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    verified_at        TEXT,
    verified_by        TEXT    CHECK (verified_by IN ('auto','treasurer','manual_override'))
);

CREATE TABLE IF NOT EXISTS admins (
    telegram_user_id INTEGER PRIMARY KEY,
    role             TEXT    NOT NULL CHECK (role IN ('treasurer','admin')),
    added_by         INTEGER,
    added_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start  TEXT,
    period_end    TEXT,
    payment_count INTEGER NOT NULL,
    result        TEXT,
    audited_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    # check_same_thread=False: the connection is created at startup but used
    # from PTB's event loop; SQLite itself is fine with this single-loop use.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_member(conn: sqlite3.Connection, telegram_user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM members WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()


def get_member_by_sutd_id(conn: sqlite3.Connection, sutd_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM members WHERE sutd_id = ?", (sutd_id,)
    ).fetchone()


def add_member(
    conn: sqlite3.Connection,
    *,
    telegram_user_id: int,
    full_name: str,
    sutd_id: str,
    username: str | None,
) -> None:
    conn.execute(
        "INSERT INTO members (telegram_user_id, full_name, sutd_id, username)"
        " VALUES (?, ?, ?, ?)",
        (telegram_user_id, full_name, sutd_id, username),
    )
    conn.commit()


def ensure_treasurer(conn: sqlite3.Connection, telegram_user_id: int) -> None:
    """Bootstrap the treasurer role from config on first run.

    If a treasurer already exists in the DB it wins — changing treasurer is
    /transfertreasurer's job (Phase 4), not the .env file's.
    """
    row = conn.execute(
        "SELECT telegram_user_id FROM admins WHERE role = 'treasurer'"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO admins (telegram_user_id, role, added_by) VALUES (?, 'treasurer', ?)",
            (telegram_user_id, telegram_user_id),
        )
        conn.commit()


def get_role(conn: sqlite3.Connection, telegram_user_id: int) -> str | None:
    row = conn.execute(
        "SELECT role FROM admins WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()
    return row["role"] if row else None


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
.venv\Scripts\python -m pytest tests/test_db.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add clubbot/db.py tests/test_db.py
git commit -m "feat: SQLite schema (PRD §8) and member/admin/settings queries"
```

---

### Task 6: `validation.py` — input validators

**Files:**
- Create: `clubbot/validation.py`
- Test: `tests/test_validation.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_validation.py` (complete file):

```python
from clubbot import validation


def test_name_normalizes_whitespace():
    assert validation.normalize_full_name("  Alice   Tan ") == "Alice Tan"


def test_name_rejects_garbage():
    assert validation.normalize_full_name("a") is None          # too short
    assert validation.normalize_full_name("12345") is None      # no letters
    assert validation.normalize_full_name("x" * 81) is None     # too long


def test_name_allows_non_ascii():
    assert validation.normalize_full_name("陈伟明") == "陈伟明"


def test_sutd_id_accepts_seven_digits():
    assert validation.normalize_sutd_id(" 1007654 ") == "1007654"


def test_sutd_id_rejects_bad_input():
    assert validation.normalize_sutd_id("100765") is None       # 6 digits
    assert validation.normalize_sutd_id("10076545") is None     # 8 digits
    assert validation.normalize_sutd_id("abcdefg") is None      # not digits
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_validation.py -v
```

Expected: FAIL — no module `validation`.

- [ ] **Step 3: Write the implementation**

`clubbot/validation.py` (complete file):

```python
"""Validators for registration input. Permissive on names (non-ASCII names
exist in the club), strict on SUTD IDs (7 digits)."""

import re

_SUTD_ID_RE = re.compile(r"\d{7}")


def normalize_full_name(text: str) -> str | None:
    name = " ".join(text.split())
    if not 2 <= len(name) <= 80:
        return None
    if not any(ch.isalpha() for ch in name):
        return None
    return name


def normalize_sutd_id(text: str) -> str | None:
    sutd_id = text.strip()
    return sutd_id if _SUTD_ID_RE.fullmatch(sutd_id) else None
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
.venv\Scripts\python -m pytest tests/test_validation.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add clubbot/validation.py tests/test_validation.py
git commit -m "feat: registration input validators"
```

---

### Task 7: `bot.py` — registration conversation, /status, /help

**Files:**
- Create: `clubbot/bot.py`
- Test: `tests/test_bot.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_bot.py` (complete file):

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from clubbot import bot, db


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def make_update(user_id=111, text=None, username="alice"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def make_context(conn):
    context = MagicMock()
    context.bot_data = {"db": conn}
    context.user_data = {}
    return context


def reply_text_of(update) -> str:
    return update.message.reply_text.call_args.args[0]


def test_start_unregistered_asks_for_name(conn):
    update, context = make_update(text="/start"), make_context(conn)
    assert asyncio.run(bot.cmd_start(update, context)) == bot.ASK_NAME
    assert "full name" in reply_text_of(update).lower()


def test_start_when_registered_shows_status(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    update, context = make_update(text="/start"), make_context(conn)
    assert asyncio.run(bot.cmd_start(update, context)) == ConversationHandler.END
    assert "registered as Alice Tan" in reply_text_of(update)


def test_full_registration_flow(conn):
    context = make_context(conn)
    asyncio.run(bot.cmd_start(make_update(text="/start"), context))

    update = make_update(text="Alice Tan")
    assert asyncio.run(bot.on_name(update, context)) == bot.ASK_SUTD_ID

    update = make_update(text="1007654")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.CONFIRM
    assert "1007654" in reply_text_of(update)

    update = make_update(text="yes")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END

    member = db.get_member(conn, 111)
    assert member["full_name"] == "Alice Tan"
    assert member["sutd_id"] == "1007654"
    assert member["username"] == "alice"


def test_invalid_name_reprompts(conn):
    context = make_context(conn)
    update = make_update(text="12345")
    assert asyncio.run(bot.on_name(update, context)) == bot.ASK_NAME


def test_invalid_sutd_id_reprompts(conn):
    context = make_context(conn)
    context.user_data["full_name"] = "Alice Tan"
    update = make_update(text="not-an-id")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.ASK_SUTD_ID


def test_duplicate_sutd_id_blocked(conn):
    db.add_member(
        conn, telegram_user_id=999, full_name="Bob Lim", sutd_id="1007654", username=None
    )
    context = make_context(conn)
    context.user_data["full_name"] = "Alice Tan"
    update = make_update(text="1007654")
    assert asyncio.run(bot.on_sutd_id(update, context)) == bot.ASK_SUTD_ID
    assert "already registered" in reply_text_of(update)


def test_confirm_no_cancels(conn):
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1007654"})
    update = make_update(text="no")
    assert asyncio.run(bot.on_confirm(update, context)) == ConversationHandler.END
    assert db.get_member(conn, 111) is None


def test_confirm_gibberish_reprompts(conn):
    context = make_context(conn)
    context.user_data.update({"full_name": "Alice Tan", "sutd_id": "1007654"})
    update = make_update(text="maybe")
    assert asyncio.run(bot.on_confirm(update, context)) == bot.CONFIRM


def test_status_unregistered(conn):
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    assert "not registered" in reply_text_of(update)


def test_status_registered(conn):
    db.add_member(
        conn, telegram_user_id=111, full_name="Alice Tan", sutd_id="1007654", username="alice"
    )
    update, context = make_update(text="/status"), make_context(conn)
    asyncio.run(bot.cmd_status(update, context))
    assert "Alice Tan" in reply_text_of(update)


def test_build_application_smoke(conn):
    app = bot.build_application("1234567:TESTTOKEN", conn)
    assert app.bot_data["db"] is conn
    assert len(app.handlers[0]) == 3  # conversation + /status + /help
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python -m pytest tests/test_bot.py -v
```

Expected: FAIL — no module `bot`.

- [ ] **Step 3: Write the implementation**

`clubbot/bot.py` (complete file):

```python
"""Telegram handlers and application wiring (Phase 1: registration + status)."""

import sqlite3

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from clubbot import db, validation

ASK_NAME, ASK_SUTD_ID, CONFIRM = range(3)

WELCOME = (
    "Welcome to the SUTD Badminton Club bot! 🏸\n\n"
    "Let's get you registered.\n"
    "What's your full name, as in SUTD records?"
)
BAD_NAME = "That doesn't look like a name — please send your full name as text (e.g. Alice Tan)."
ASK_ID_TEXT = "Thanks! Now send your 7-digit SUTD student ID (e.g. 1007654)."
BAD_SUTD_ID = "That doesn't look right — your SUTD student ID is exactly 7 digits (e.g. 1007654)."
SUTD_ID_TAKEN = (
    "That SUTD ID is already registered to a different Telegram account.\n"
    "If you switched accounts, ask the treasurer to /relink you. Use /cancel to stop."
)
CONFIRM_PROMPT = (
    "Register as:\n\n  Name: {name}\n  SUTD ID: {sutd_id}\n\n"
    "Reply yes to confirm or no to start over."
)
REGISTERED = (
    "You're registered, {name}! ✅\n"
    "You'll get a message here when membership fee collection opens. Check /status any time."
)
CANCELLED = "Registration cancelled. Send /start whenever you're ready."
NOT_REGISTERED = "You're not registered yet — send /start to register."
HELP_TEXT = (
    "Commands:\n"
    "/start — register (or see your status)\n"
    "/status — your membership status\n"
    "/help — this message\n\n"
    "Paying for membership will be added soon."
)


def _db(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    return context.bot_data["db"]


def _status_text(member: sqlite3.Row) -> str:
    return (
        f"You're registered as {member['full_name']} (SUTD ID {member['sutd_id']}).\n"
        "No membership term is open yet — you'll be messaged when fee collection starts."
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    member = db.get_member(_db(context), update.effective_user.id)
    if member is not None:
        await update.message.reply_text(_status_text(member))
        return ConversationHandler.END
    await update.message.reply_text(WELCOME)
    return ASK_NAME


async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = validation.normalize_full_name(update.message.text)
    if name is None:
        await update.message.reply_text(BAD_NAME)
        return ASK_NAME
    context.user_data["full_name"] = name
    await update.message.reply_text(ASK_ID_TEXT)
    return ASK_SUTD_ID


async def on_sutd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sutd_id = validation.normalize_sutd_id(update.message.text)
    if sutd_id is None:
        await update.message.reply_text(BAD_SUTD_ID)
        return ASK_SUTD_ID
    if db.get_member_by_sutd_id(_db(context), sutd_id) is not None:
        await update.message.reply_text(SUTD_ID_TAKEN)
        return ASK_SUTD_ID
    context.user_data["sutd_id"] = sutd_id
    await update.message.reply_text(
        CONFIRM_PROMPT.format(name=context.user_data["full_name"], sutd_id=sutd_id)
    )
    return CONFIRM


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer in ("yes", "y"):
        user = update.effective_user
        db.add_member(
            _db(context),
            telegram_user_id=user.id,
            full_name=context.user_data["full_name"],
            sutd_id=context.user_data["sutd_id"],
            username=user.username,
        )
        name = context.user_data["full_name"]
        context.user_data.clear()
        await update.message.reply_text(REGISTERED.format(name=name))
        return ConversationHandler.END
    if answer in ("no", "n"):
        context.user_data.clear()
        await update.message.reply_text(CANCELLED)
        return ConversationHandler.END
    await update.message.reply_text("Please reply yes or no (or /cancel).")
    return CONFIRM


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(CANCELLED)
    return ConversationHandler.END


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member = db.get_member(_db(context), update.effective_user.id)
    if member is None:
        await update.message.reply_text(NOT_REGISTERED)
    else:
        await update.message.reply_text(_status_text(member))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


def build_application(token: str, conn: sqlite3.Connection) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["db"] = conn
    registration = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_name)],
            ASK_SUTD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_sutd_id)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(registration)
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    return app
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
.venv\Scripts\python -m pytest tests/test_bot.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add clubbot/bot.py tests/test_bot.py
git commit -m "feat: registration conversation, /status and /help handlers"
```

---

### Task 8: `config.py` + `__main__.py` + env template + README

**Files:**
- Create: `clubbot/config.py`, `clubbot/__main__.py`, `.env.example`, `README.md`

- [ ] **Step 1: Write `clubbot/config.py`**

```python
"""Configuration from .env / environment variables."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    treasurer_id: int
    db_path: str


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("BOT_TOKEN", "")
    treasurer = os.environ.get("TREASURER_TELEGRAM_ID", "")
    if not token:
        raise SystemExit("BOT_TOKEN is missing — copy .env.example to .env and fill it in.")
    if not treasurer.isdigit():
        raise SystemExit("TREASURER_TELEGRAM_ID is missing or not a number in .env.")
    return Config(
        bot_token=token,
        treasurer_id=int(treasurer),
        db_path=os.environ.get("DB_PATH", "clubbot.db"),
    )
```

- [ ] **Step 2: Write `clubbot/__main__.py`**

```python
"""Entry point: python -m clubbot"""

import logging

from clubbot import bot, config, db


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
    )
    cfg = config.load_config()
    conn = db.connect(cfg.db_path)
    db.ensure_treasurer(conn, cfg.treasurer_id)
    app = bot.build_application(cfg.bot_token, conn)
    app.run_polling()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write `.env.example`**

```
# Telegram bot token from @BotFather
BOT_TOKEN=123456789:replace-with-real-token
# Your own numeric Telegram user ID (message @userinfobot to find it)
TREASURER_TELEGRAM_ID=12345678
# SQLite file path (default is fine)
DB_PATH=clubbot.db
```

- [ ] **Step 4: Write `README.md`**

```markdown
# SUTD Badminton Club Bot

Telegram bot that registers club members, collects term fees via PayNow QR,
and verifies payment screenshots automatically. Design doc:
`docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`. Living status:
`MEMORY.md`.

## Setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
# edit .env: bot token from @BotFather, your Telegram ID from @userinfobot
```

## Run the bot

```powershell
.venv\Scripts\python -m clubbot
```

Stop with Ctrl+C. The database is a single file (`clubbot.db`).

## Run the tests

```powershell
.venv\Scripts\python -m pytest
```

## Phase 0: QR placement test (do this once)

```powershell
.venv\Scripts\python scripts/make_phase0_qrs.py
```

Pay S$0.10 with each generated QR (`phase0_qrs/`) from your personal bank
app, then check the DBS Flimax history and report what each payment shows.
The outcome unblocks the payment engine (Phase 2).
```

- [ ] **Step 5: Run the full test suite**

```powershell
.venv\Scripts\python -m pytest -v
```

Expected: every test from Tasks 2–7 passes.

- [ ] **Step 6: Smoke-test config failure mode (no .env present)**

```powershell
.venv\Scripts\python -m clubbot
```

Expected: exits immediately with `BOT_TOKEN is missing — copy .env.example to .env and fill it in.` (Proves the entry point wires up; a real run needs the user's bot token.)

- [ ] **Step 7: Commit**

```powershell
git add clubbot/config.py clubbot/__main__.py .env.example README.md
git commit -m "feat: config loading, entry point, env template, README"
```

---

### Task 9: Update MEMORY.md and merge

**Files:**
- Modify: `MEMORY.md` (Current status + Next steps sections)

- [ ] **Step 1: Update `MEMORY.md`**

Add to **Current status**: date, "Phase 0 tooling + Phase 1 core bot implemented and tested (registration, SQLite, /status, /help, treasurer bootstrap). Test QRs generated in `phase0_qrs/` — waiting on treasurer's two S$0.10 test payments + Flimax check. Phase 2 still blocked on that outcome."

Update **Next steps**: (1) user creates bot via @BotFather + fills `.env` + runs bot; (2) user pays the two test QRs and reports Flimax results; (3) record outcome → write Phase 2+ plan.

- [ ] **Step 2: Commit**

```powershell
git add MEMORY.md
git commit -m "docs: record Phase 0 tooling + Phase 1 completion in MEMORY.md"
```

- [ ] **Step 3: Merge to master** (per superpowers:finishing-a-development-branch)

```powershell
git checkout master
git merge phase0-phase1-core-bot
```

---

## Self-review notes

- **Spec coverage:** Phase 0 item 2 (variant A/B QRs) → Task 4. Phase 1 (registration, SQLite, /status, admin bootstrap) → Tasks 5–8. PRD §8 schema → Task 5 (full schema created now so later phases migrate nothing). PRD §14 unit tests for EMVCo builder/CRC vs known-good vector → Task 2. Phases 2–5 intentionally excluded (Phase 0 outcome unknown; separate plan).
- **Out of scope, deliberately:** payments table is created but unused; `/pay`, terms, reminders, Gemini, Sheets all wait for the Phase 2+ plan.
- **Consistency:** `build_payload` signature identical in Tasks 2/3/4; `db.add_member` keyword args identical in Tasks 5/7; states `ASK_NAME/ASK_SUTD_ID/CONFIRM` shared between bot and tests.
