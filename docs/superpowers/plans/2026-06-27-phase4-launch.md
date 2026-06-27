# Phase 4 Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the SUTD Badminton Club bot for production: admin management, account relink, editable PayNow settings, a Google Sheet mirror, and a robustness layer — then ship a go-live guide.

**Architecture:** A coupled **core** (db schema/queries, a backward-compatible `payments.py` config refactor, and a new `ops.py` robustness module) is built first because every leaf depends on it. Then three **disjoint leaf modules** (`admin_manage.py`, `paynow_config.py`, `sheets.py`) are built in parallel, each importing only the core. Finally `bot.build_application` + `__main__` wire everything and add Sheet-sync hooks.

**Tech Stack:** Python 3.12, python-telegram-bot v21+ (long-polling, JobQueue), SQLite, google-genai, gspread + google-auth, pytest.

**Conventions (match existing code):** handlers are thin (`permission → db → reply`); `_db(context)` returns the connection; permission via `db.get_role`; SUTD-ID lookups via `admin._resolve_member`; tests use `db.connect(":memory:")`, `MagicMock`/`AsyncMock`, and `asyncio.run`. Keep files focused and readable.

---

## CORE SPINE (implemented first — leaves depend on these exact signatures)

### Task 1: Fix the red test suite + harden registration

**Files:**
- Modify: `tests/test_validation.py`, `tests/test_bot.py`
- Modify: `clubbot/bot.py` (`on_confirm`)

- [ ] **Step 1 — Fix validation fixture.** In `tests/test_validation.py::test_sutd_id_accepts_seven_digits` replace `" 1007654 "`/`"1007654"` with a `1010`-prefixed ID, e.g. `" 1010765 "` → `"1010765"`.

- [ ] **Step 2 — Fix registration fixtures.** In `tests/test_bot.py`, in `test_full_registration_flow` and `test_duplicate_sutd_id_blocked`, replace every `"1007654"` that flows through `on_sutd_id` with `"1010765"` (and the pre-seeded member's `sutd_id` in the duplicate test to `"1010765"` so the "already registered" branch is reached).

- [ ] **Step 3 — Run, expect green.** `\.venv\Scripts\python -m pytest tests/test_validation.py tests/test_bot.py -q` → PASS.

- [ ] **Step 4 — Harden `on_confirm` against a duplicate-SUTD race.** Wrap the `db.add_member` call:

```python
    if answer in ("yes", "y"):
        user = update.effective_user
        try:
            db.add_member(
                _db(context),
                telegram_user_id=user.id,
                full_name=context.user_data["full_name"],
                sutd_id=context.user_data["sutd_id"],
                username=user.username,
            )
        except sqlite3.IntegrityError:
            context.user_data.clear()
            await update.message.reply_text(SUTD_ID_TAKEN)
            return ConversationHandler.END
        name = context.user_data["full_name"]
        context.user_data.clear()
        await update.message.reply_text(REGISTERED.format(name=name))
        return ConversationHandler.END
```

- [ ] **Step 5 — Full suite green.** `\.venv\Scripts\python -m pytest -q` → 107 passed.

- [ ] **Step 6 — Commit.** `git add -A && git commit -m "fix: correct SUTD-ID test fixtures and harden registration race"`

---

### Task 2: DB schema + queries (admins, relink, durability)

**Files:**
- Modify: `clubbot/db.py`
- Test: `tests/test_db.py` (append)

- [ ] **Step 1 — Durability in `connect()`.** After `conn.row_factory = sqlite3.Row` and before `executescript`, add:

```python
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
```
(Replace the existing single `PRAGMA foreign_keys = ON` line; keep one.)

- [ ] **Step 2 — Add `relink_requests` to `SCHEMA`:**

```sql
CREATE TABLE IF NOT EXISTS relink_requests (
    sutd_id              TEXT    PRIMARY KEY,
    new_telegram_user_id INTEGER NOT NULL,
    new_username         TEXT,
    requested_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 3 — Write failing tests** in `tests/test_db.py`:

```python
def test_admin_add_list_remove(conn):
    db.add_member(conn, telegram_user_id=111, full_name="A", sutd_id="1010001", username=None)
    db.ensure_treasurer(conn, 999)
    db.add_admin(conn, telegram_user_id=111, added_by=999)
    assert db.get_role(conn, 111) == "admin"
    assert 111 in {a["telegram_user_id"] for a in db.list_admins(conn)}
    assert db.remove_admin(conn, 111) is True
    assert db.get_role(conn, 111) is None
    # never removes the treasurer
    assert db.remove_admin(conn, 999) is False
    assert db.get_role(conn, 999) == "treasurer"


def test_transfer_treasurer_is_atomic(conn):
    db.add_member(conn, telegram_user_id=111, full_name="A", sutd_id="1010001", username=None)
    db.ensure_treasurer(conn, 999)
    db.transfer_treasurer(conn, new_treasurer_id=111, added_by=999)
    assert db.get_role(conn, 111) == "treasurer"
    assert db.get_role(conn, 999) == "admin"
    assert db.get_treasurer_id(conn) == 111
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM admins WHERE role='treasurer'"
    ).fetchone()["n"] == 1


def test_relink_request_roundtrip(conn):
    db.upsert_relink_request(conn, sutd_id="1010001", new_telegram_user_id=222, new_username="newacct")
    req = db.get_relink_request(conn, "1010001")
    assert req["new_telegram_user_id"] == 222 and req["new_username"] == "newacct"
    db.upsert_relink_request(conn, sutd_id="1010001", new_telegram_user_id=333, new_username="newer")
    assert db.get_relink_request(conn, "1010001")["new_telegram_user_id"] == 333
    db.delete_relink_request(conn, "1010001")
    assert db.get_relink_request(conn, "1010001") is None


def test_reassign_member_preserves_payments(conn):
    db.add_member(conn, telegram_user_id=111, full_name="A", sutd_id="1010001", username="old")
    today = date.today()
    term = db.create_term(conn, name="T", fee_cents=2000,
                          start_date=(today - timedelta(days=1)).isoformat(),
                          end_date=(today + timedelta(days=10)).isoformat(), created_by=999)
    db.mark_paid_manual(conn, member_id=111, term_id=term["id"])
    db.reassign_member_telegram_id(conn, old_id=111, new_id=222, new_username="new")
    assert db.get_member(conn, 111) is None
    moved = db.get_member(conn, 222)
    assert moved["sutd_id"] == "1010001" and moved["username"] == "new"
    pay = db.get_payment_for_member_term(conn, member_id=222, term_id=term["id"])
    assert pay is not None and pay["status"] == "verified"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
```

- [ ] **Step 4 — Run, expect failures** (`AttributeError`): `\.venv\Scripts\python -m pytest tests/test_db.py -q`.

- [ ] **Step 5 — Implement the queries** in `clubbot/db.py`:

```python
def add_admin(conn, *, telegram_user_id, added_by):
    conn.execute(
        "INSERT OR IGNORE INTO admins (telegram_user_id, role, added_by)"
        " VALUES (?, 'admin', ?)",
        (telegram_user_id, added_by),
    )
    conn.commit()


def remove_admin(conn, telegram_user_id) -> bool:
    """Remove an 'admin' row. Never touches the treasurer. True if a row went."""
    cur = conn.execute(
        "DELETE FROM admins WHERE telegram_user_id = ? AND role = 'admin'",
        (telegram_user_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def list_admins(conn):
    return conn.execute(
        "SELECT * FROM admins ORDER BY role DESC, added_at"
    ).fetchall()


def transfer_treasurer(conn, *, new_treasurer_id, added_by):
    """Atomically demote the sitting treasurer to admin and promote the target."""
    with conn:  # implicit transaction; rolls back on error
        conn.execute("UPDATE admins SET role = 'admin' WHERE role = 'treasurer'")
        conn.execute(
            "INSERT INTO admins (telegram_user_id, role, added_by) VALUES (?, 'treasurer', ?)"
            " ON CONFLICT(telegram_user_id) DO UPDATE SET role = 'treasurer'",
            (new_treasurer_id, added_by),
        )


def upsert_relink_request(conn, *, sutd_id, new_telegram_user_id, new_username):
    conn.execute(
        "INSERT INTO relink_requests (sutd_id, new_telegram_user_id, new_username)"
        " VALUES (?, ?, ?)"
        " ON CONFLICT(sutd_id) DO UPDATE SET"
        " new_telegram_user_id = excluded.new_telegram_user_id,"
        " new_username = excluded.new_username,"
        " requested_at = datetime('now')",
        (sutd_id, new_telegram_user_id, new_username),
    )
    conn.commit()


def get_relink_request(conn, sutd_id):
    return conn.execute(
        "SELECT * FROM relink_requests WHERE sutd_id = ?", (sutd_id,)
    ).fetchone()


def delete_relink_request(conn, sutd_id):
    conn.execute("DELETE FROM relink_requests WHERE sutd_id = ?", (sutd_id,))
    conn.commit()


def reassign_member_telegram_id(conn, *, old_id, new_id, new_username):
    """Move a member (and their payment/admin history) to a new Telegram id.

    Changing the members PK while payments/admins reference it needs FK
    enforcement off for the swap; toggled outside a transaction, then restored.
    """
    conn.commit()  # ensure no open transaction so the PRAGMA takes effect
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            conn.execute(
                "UPDATE members SET telegram_user_id = ?, username = ? WHERE telegram_user_id = ?",
                (new_id, new_username, old_id),
            )
            conn.execute(
                "UPDATE payments SET member_id = ? WHERE member_id = ?", (new_id, old_id)
            )
            conn.execute(
                "UPDATE admins SET telegram_user_id = ? WHERE telegram_user_id = ?",
                (new_id, old_id),
            )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
```

- [ ] **Step 6 — Run, expect green.** `\.venv\Scripts\python -m pytest tests/test_db.py -q`.

- [ ] **Step 7 — Commit.** `git add -A && git commit -m "feat(db): admin mgmt, relink requests, FK-safe member reassignment, WAL"`

---

### Task 3: `payments.py` config refactor (backward-compatible)

**Files:**
- Modify: `clubbot/payments.py`
- Test: `tests/test_payments.py` (append)

- [ ] **Step 1 — Write failing tests:**

```python
from clubbot.paynow_config import PayNowConfig  # built in Task 5; import locally in test
from clubbot import payments

def _cfg(**kw):
    base = dict(uen="UEN123", merchant_name="NEW SCHOOL",
               bill_number="BILL999", recipient_match="NEWSCHOOL")
    base.update(kw); return PayNowConfig(**base)

def test_build_qr_uses_config_bill_number():
    payload_default = payments.paynow.parse_tlv  # sanity import
    qr = payments.build_member_qr(fee_cents=2000, reference="BDM-1-X", config=_cfg())
    assert isinstance(qr, (bytes, bytearray))

def test_verify_uses_config_recipient_and_bill(make_extracted):
    # make_extracted: helper already in test_payments for ExtractedPayment
    extracted = make_extracted(recipient="NEW SCHOOL", billing_id="BILL999")
    result = payments.verify_extracted_payment(
        extracted, expected_fee_cents=2000, term_start="2026-06-01",
        term_end="2026-12-31", qr_issued_at="2026-06-10T00:00:00+00:00",
        now=__import__("datetime").datetime(2026, 6, 20, tzinfo=__import__("datetime").timezone.utc),
        config=_cfg())
    assert "Billing ID" not in " ".join(result.reasons)
    assert "Recipient" not in " ".join(result.reasons)
```
(If `test_payments.py` lacks a `make_extracted` helper/fixture, add a small one constructing a passing `ExtractedPayment`; reuse the existing test's construction style.)

- [ ] **Step 2 — Run, expect failure** (config kwarg unknown / import error).

- [ ] **Step 3 — Refactor `payments.py`.** Add an import-light indirection so `payments.py` does **not** import `paynow_config` at module load (avoid cycles); accept a duck-typed `config` with `.uen/.merchant_name/.bill_number/.recipient_match`:

```python
def build_member_qr(*, fee_cents: int, reference: str, config=None) -> bytes:
    if fee_cents <= 0:
        raise ValueError("fee must be positive")
    uen = config.uen if config else SCHOOL_UEN
    merchant_name = config.merchant_name if config else SCHOOL_MERCHANT_NAME
    bill_number = config.bill_number if config else SCHOOL_BILL_NUMBER
    payload = paynow.build_payload(
        uen=uen, merchant_name=merchant_name,
        amount=Decimal(fee_cents) / Decimal(100),
        bill_number=bill_number, reference_label=reference,
    )
    return qrgen.render_png(payload)
```
In `verify_extracted_payment`, add `config=None` to the signature and replace the two hardcoded checks:

```python
    recipient_match = config.recipient_match if config else "SINGAPOREUNIVERSITYOF"
    expected_bill = config.bill_number if config else SCHOOL_BILL_NUMBER
    ...
    if recipient_match not in recipient:
        reasons.append("Recipient does not match the SUTD account.")
    if _normalise_text(extracted.billing_id) != _normalise_text(expected_bill):
        reasons.append("Billing ID does not match the club's DBS FLYMAX account.")
```

- [ ] **Step 4 — Run, expect green** (this file's tests).

- [ ] **Step 5 — Full suite green** (ensures default-path callers unchanged): `\.venv\Scripts\python -m pytest -q`.

- [ ] **Step 6 — Commit.** `git add -A && git commit -m "refactor(payments): optional PayNowConfig for QR build and verification"`

---

### Task 4: `clubbot/ops.py` — robustness helpers

**Files:**
- Create: `clubbot/ops.py`
- Test: `tests/test_ops.py`

- [ ] **Step 1 — Write failing tests** `tests/test_ops.py`:

```python
import asyncio, sqlite3
from unittest.mock import AsyncMock, MagicMock
from clubbot import ops, db

def test_chunk_text_splits_under_limit():
    parts = ops.chunk_text("\n".join(str(i) for i in range(5000)), limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert "".join(parts).replace("\n", "") == "".join(str(i) for i in range(5000))

def test_rate_limiter_blocks_within_window():
    rl = ops.RateLimiter(max_per_window=2, window_seconds=60, min_interval_seconds=0,
                         clock=iter([0.0, 0.0, 0.0]).__next__)
    assert rl.allow("u") is True
    assert rl.allow("u") is True
    assert rl.allow("u") is False  # 3rd in window

def test_rate_limiter_min_interval():
    times = iter([0.0, 1.0]).__next__
    rl = ops.RateLimiter(max_per_window=99, window_seconds=60, min_interval_seconds=5, clock=times)
    assert rl.allow("u") is True
    assert rl.allow("u") is False  # only 1s since last

def test_backup_database_creates_readable_copy(tmp_path):
    src = tmp_path / "clubbot.db"
    conn = db.connect(str(src)); db.ensure_treasurer(conn, 999); conn.commit()
    out = ops.backup_database(str(src), backups_dir=str(tmp_path / "backups"), keep=3)
    restored = sqlite3.connect(out)
    assert restored.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1

async def _err():
    update = MagicMock(); update.effective_user = None
    context = MagicMock(); context.error = ValueError("boom")
    context.bot_data = {"db": db.connect(":memory:")}
    db.ensure_treasurer(context.bot_data["db"], 999)
    context.bot.send_message = AsyncMock()
    await ops.on_error(update, context)
    return context

def test_on_error_dms_treasurer_once():
    context = asyncio.run(_err())
    assert context.bot.send_message.await_count == 1
```

- [ ] **Step 2 — Run, expect failure** (module missing).

- [ ] **Step 3 — Implement `clubbot/ops.py`:**

```python
"""Cross-cutting robustness: error handling, rate limiting, backups, chunking."""
from __future__ import annotations

import logging, os, shutil, sqlite3, time
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from clubbot import db

log = logging.getLogger(__name__)
TELEGRAM_LIMIT = 4096


def chunk_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split text into <=limit pieces, preferring line boundaries."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:           # a single over-long line
            if cur:
                parts.append(cur); cur = ""
            parts.append(line[:limit]); line = line[limit:]
        add = line if not cur else cur + "\n" + line
        if len(add) > limit:
            parts.append(cur); cur = line
        else:
            cur = add
    if cur:
        parts.append(cur)
    return parts


async def reply_long(message, text: str) -> None:
    for part in chunk_text(text):
        await message.reply_text(part)


class RateLimiter:
    """In-memory per-key limiter: a max count per rolling window + min interval."""

    def __init__(self, *, max_per_window: int, window_seconds: float,
                 min_interval_seconds: float = 0.0, clock=time.monotonic):
        self.max = max_per_window
        self.window = window_seconds
        self.min_interval = min_interval_seconds
        self._clock = clock
        self._hits: dict[object, list[float]] = {}

    def allow(self, key) -> bool:
        now = self._clock()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if hits and self.min_interval and now - hits[-1] < self.min_interval:
            self._hits[key] = hits
            return False
        if len(hits) >= self.max:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


def backup_database(db_path: str, *, backups_dir: str = "backups", keep: int = 14) -> str:
    """Consistent online backup via the SQLite backup API; prune to the newest `keep`."""
    os.makedirs(backups_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = os.path.join(backups_dir, f"clubbot-{stamp}.db")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    backups = sorted(
        (f for f in os.listdir(backups_dir) if f.startswith("clubbot-") and f.endswith(".db")),
        reverse=True,
    )
    for stale in backups[keep:]:
        os.remove(os.path.join(backups_dir, stale))
    return out


_last_alert = {"t": 0.0}
_ALERT_INTERVAL = 300.0  # seconds between treasurer error DMs


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error", exc_info=context.error)
    now = time.monotonic()
    if now - _last_alert["t"] < _ALERT_INTERVAL:
        return
    _last_alert["t"] = now
    conn = context.bot_data.get("db")
    if conn is None:
        return
    treasurer_id = db.get_treasurer_id(conn)
    if treasurer_id is None:
        return
    try:
        await context.bot.send_message(
            chat_id=treasurer_id,
            text=f"⚠️ The bot hit an internal error: {type(context.error).__name__}. "
                 "It is still running; check the logs if this repeats.",
        )
    except Exception:
        log.warning("Could not DM treasurer about an error", exc_info=True)


def mark_dirty(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger a Google Sheet rebuild if a syncer is configured; otherwise no-op."""
    syncer = context.bot_data.get("sheet_syncer")
    if syncer is not None:
        syncer.mark_dirty()
```

- [ ] **Step 4 — Run, expect green.** `\.venv\Scripts\python -m pytest tests/test_ops.py -q`.

- [ ] **Step 5 — Commit.** `git add -A && git commit -m "feat(ops): error handler, rate limiter, backups, message chunking"`

---

## LEAF MODULES (built in parallel — each imports only the core above)

### Task 5: `clubbot/paynow_config.py` — settings-backed config + `/settings`

**Files:**
- Create: `clubbot/paynow_config.py`
- Test: `tests/test_paynow_config.py`

**Interface (exact):**
- `PayNowConfig(uen, merchant_name, bill_number, recipient_match)` — frozen dataclass.
- `SETTING_KEYS = {"paynow_uen":"uen", "merchant_name":"merchant_name", "bill_number":"bill_number", "recipient_match":"recipient_match"}`
- `EDITABLE_KEYS = set(SETTING_KEYS)`; `ROUTING_CRITICAL = {"paynow_uen", "bill_number"}`
- `get_paynow_config(conn) -> PayNowConfig` — each field = `db.get_setting(conn, key)` or the default from `payments.SCHOOL_*` (`recipient_match` default `"SINGAPOREUNIVERSITYOF"`).
- `set_value(conn, key, value) -> None` — raise `ValueError` if `key not in EDITABLE_KEYS` or `value` blank; else `db.set_setting`.
- Handlers: `cmd_settings(update, context)` (no args → show config to any admin; `set <key> <value>` → treasurer-only, routing-critical keys go through a confirm button), `on_settings_confirm(update, context)` (callback `^settings:(confirm|cancel)$`, re-checks treasurer, applies the value stashed in `context.bot_data["pending_settings"][uid]`).

- [ ] **Step 1 — Tests** `tests/test_paynow_config.py`: (a) defaults equal the `SCHOOL_*` constants when settings empty; (b) `get_paynow_config` reflects a `db.set_setting`; (c) `set_value` rejects unknown key and blank value; (d) `cmd_settings` no-arg shows current values to an admin; (e) `cmd_settings set merchant_name X` (non-critical) applies immediately and replies success; (f) `cmd_settings set bill_number Y` as treasurer replies with a warning + Confirm button and does **not** yet change the value; (g) `on_settings_confirm` confirm applies it; (h) non-treasurer `set` is refused. Use the `make_update/make_context/reply_text_of` patterns from `tests/test_admin.py` (copy those helpers into this test file).

- [ ] **Step 2 — Run, expect failure.**

- [ ] **Step 3 — Implement** per the interface above. Confirm-flow: stash `{"key":..,"value":..}` in `context.bot_data.setdefault("pending_settings", {})[update.effective_user.id]`; the callback reads + clears it. Warning text must mention Phase 0 ("changing this can stop payments reaching DBS FLYMAX"). Reuse `db.get_role` for permissions.

- [ ] **Step 4 — Run green; Step 5 — full suite green; Step 6 — Commit** `feat(settings): editable PayNow config with confirm for routing-critical keys`.

---

### Task 6: `clubbot/admin_manage.py` — `/addadmin /removeadmin /transfertreasurer /relink`

**Files:**
- Create: `clubbot/admin_manage.py`
- Test: `tests/test_admin_manage.py`

**Behavior (treasurer-only unless noted; mirror `admin.py` thin style; use `admin._resolve_member` for SUTD lookups, or replicate it):**

- `cmd_addadmin` — resolve member by `args[0]` (SUTD ID); if `db.get_role` already set → reply "already an admin/treasurer"; else `db.add_admin(conn, telegram_user_id=member_id, added_by=uid)`, reply success, DM the new admin.
- `cmd_removeadmin` — resolve member; `if db.get_role(conn, member_id) == "treasurer"` → refuse; else `removed = db.remove_admin(...)`; reply accordingly.
- `cmd_transfertreasurer` — resolve member; refuse if target already treasurer; stash `{"new_id":member_id,"sutd_id":..}` in `bot_data["pending_transfer"][uid]`; reply with **Confirm/Cancel** inline keyboard (`callback_data="transfer:confirm"`/`"transfer:cancel"`) and a clear warning.
- `on_transfer_confirm` — callback `^transfer:(confirm|cancel)$`; re-check caller is current treasurer; on confirm read+clear the stash, call `db.transfer_treasurer(conn, new_treasurer_id=new_id, added_by=uid)`, DM both, edit the message.
- `cmd_relink` — treasurer-only. `args[0]`=sutd_id (member must exist). If `len(args) >= 2` use `int(args[1])` as new id; else look up `db.get_relink_request(conn, sutd_id)` (error if none). Guard: new id != old id; `db.get_member(conn, new_id)` must be None (else "that Telegram account already belongs to a member"). Stash `{"sutd_id":..,"old_id":..,"new_id":..,"new_username":..}` in `bot_data["pending_relink"][uid]`; reply Confirm/Cancel (`relink:confirm`/`relink:cancel`) showing old→new.
- `on_relink_confirm` — callback `^relink:(confirm|cancel)$`; re-check treasurer; on confirm: `db.reassign_member_telegram_id(...)`, `db.delete_relink_request(conn, sutd_id)`, DM the new account, edit message.

- [ ] **Step 1 — Tests** `tests/test_admin_manage.py` covering: addadmin happy + non-member + already-admin + non-treasurer refusal; removeadmin removes admin, refuses on treasurer, handles non-admin; transfer confirm path makes target treasurer & demotes old (assert via `db.get_role`), cancel path leaves roles, non-treasurer refused; relink with explicit id reassigns and preserves a verified payment (seed one via `mark_paid_manual`) and DMs new id; relink via pending request; relink refuses when new id already a member. Copy the `make_update/make_context/make_callback_update/reply_text_of/edit_text_of` helpers from `tests/test_admin.py`; for callback tests set `update.callback_query.data` appropriately.

- [ ] **Step 2 — Run, expect failure. Step 3 — Implement. Step 4 — green. Step 5 — full suite green. Step 6 — Commit** `feat(admin): addadmin/removeadmin/transfertreasurer/relink with confirms`.

---

### Task 7: `clubbot/sheets.py` — Google Sheet mirror

**Files:**
- Create: `clubbot/sheets.py`
- Test: `tests/test_sheets.py`

**Interface (exact):**
- `member_rows(conn) -> list[list]` — header + one row per `db.list_members`: `["Full name","SUTD ID","Username","Telegram ID","Active","Joined"]`.
- `payment_rows(conn) -> list[list]` — header + one row per payment joined to member+term: `["Member","SUTD ID","Term","Status","Amount","Verified by","Payment time","Flagged","Audit confirmed"]`. Add a `db.list_all_payments(conn)` query (append to Task 2 file if not present — returns `get_payment`-shaped rows across all terms ordered by term then name).
- `class SheetMirror`: `__init__(self, *, service_account_json, sheet_id, client=None)` — when `client is None`, lazy-import `gspread`/`google.oauth2.service_account` and authorize; store the opened spreadsheet. `full_rebuild(self, conn)` — write `member_rows` to a "Members" worksheet and `payment_rows` to "Payments" (clear then update; create the worksheet if missing).
- `class SheetSyncer`: `__init__(self, mirror, conn, *, debounce_seconds=5.0)`; `mark_dirty(self)` — schedule one coalesced rebuild (set a flag + `asyncio.create_task` running `await asyncio.sleep(debounce); await asyncio.to_thread(mirror.full_rebuild, conn)`); guard so overlapping marks don't stack more than one pending run; wrap the rebuild so exceptions are logged, never raised. Expose `async def _run_now(self)` for tests.
- `create_mirror_from_env(service_account_json, sheet_id) -> SheetMirror | None` — return `None` if either arg is falsy; catch construction errors, log, return `None`.

- [ ] **Step 1 — Tests** `tests/test_sheets.py` (no network — inject a fake `client`/mirror): (a) `member_rows`/`payment_rows` produce header + expected data from a seeded in-memory db; (b) `full_rebuild` calls the fake worksheet's clear+update with those rows (fake spreadsheet exposing `worksheet`/`add_worksheet`/`clear`/`update`); (c) `SheetSyncer.mark_dirty` called 3× rapidly results in exactly one `full_rebuild` (use a mirror MagicMock and `asyncio.run` driving `_run_now`, or a 0-second debounce); (d) a mirror whose `full_rebuild` raises does not propagate out of the syncer; (e) `create_mirror_from_env("", "")` returns None.

- [ ] **Step 2 — Run, expect failure. Step 3 — Implement** (lazy imports so the module loads without gspread installed; all network behind the injected client). **Step 4 — green. Step 5 — full suite green. Step 6 — Commit** `feat(sheets): coalesced, failure-isolated one-way Google Sheet mirror`.

---

## INTEGRATION (after core + leaves are green)

### Task 8: Wire handlers, sync hooks, config, env

**Files:**
- Modify: `clubbot/bot.py` (`build_application`, `cmd_pay`, `on_receipt`, help text, registration `SUTD_ID_TAKEN` branch records a relink request)
- Modify: `clubbot/scheduler.py` (`do_term_start_blast` passes config; add nightly backup + sheet-rebuild jobs)
- Modify: `clubbot/admin.py` (`/members`,`/unpaid` use `ops.reply_long`; add `/sync`,`/backup`)
- Modify: `clubbot/__main__.py` (build mirror/syncer/rate limiter; register error handler)
- Modify: `requirements.txt`, `.env.example`

- [ ] **Step 1 — Register handlers** in `build_application`: commands `addadmin, removeadmin, transfertreasurer, relink` → `admin_manage`; `settings` → `paynow_config.cmd_settings`; `sync, backup` → `admin`; callbacks for `^transfer:(confirm|cancel)$`, `^relink:(confirm|cancel)$`, `^settings:(confirm|cancel)$`. Store `app.bot_data["sheet_syncer"]`, `["rate_limiter"]`. `app.add_error_handler(ops.on_error)`. Extend `HELP_TEXT`/`ADMIN_HELP` with the new commands.

- [ ] **Step 2 — PayNow config in QR/verify paths:** in `cmd_pay`, `on_receipt`, and `scheduler.do_term_start_blast`, compute `cfg = paynow_config.get_paynow_config(conn)` and pass `config=cfg` to `build_member_qr` and `verify_extracted_payment`.

- [ ] **Step 3 — Rate-limit the receipt path:** at the top of `on_receipt`, after confirming there's an active term/payment, `rl = context.bot_data.get("rate_limiter")`; if `rl and not rl.allow(update.effective_user.id)`: reply "Please wait a moment before sending another receipt." and return (before any Gemini call).

- [ ] **Step 4 — Relink capture:** in `on_sutd_id`, the `SUTD_ID_TAKEN` branch also calls `db.upsert_relink_request(conn, sutd_id=sutd_id, new_telegram_user_id=update.effective_user.id, new_username=update.effective_user.username)`.

- [ ] **Step 5 — Sheet sync hooks:** call `ops.mark_dirty(context)` after successful: registration (`on_confirm`), receipt verified (`on_receipt`), `admin.cmd_markpaid`, `admin.cmd_flag`, `admin.cmd_revoke`, `on_payment_review` approve, `cmd_newterm`, and relink/transfer confirms. (Scheduler jobs call `syncer.mark_dirty()` directly via `context.bot_data`.)

- [ ] **Step 6 — `/sync` and `/backup`** in `admin.py` (treasurer-only): `/sync` → `syncer = context.bot_data.get("sheet_syncer")`; if None reply "Sheet mirror is not configured."; else `syncer.mark_dirty()` + reply "Sheet refresh queued." `/backup` → `path = ops.backup_database(context.bot_data["db_path"]); reply path`. Store `db_path` in `bot_data` (Task 8 Step 8).

- [ ] **Step 7 — Scheduler jobs:** in `schedule_all`, add `jq.run_daily(_job_backup, time=03:00 SGT)` and `jq.run_daily(_job_sheet_rebuild, time=04:00 SGT)`; implement `_job_backup` (calls `ops.backup_database(context.bot_data["db_path"])`) and `_job_sheet_rebuild` (`s=context.bot_data.get("sheet_syncer"); if s: s.mark_dirty()`).

- [ ] **Step 8 — `__main__.py`:** build `mirror = sheets.create_mirror_from_env(cfg.google_service_account_json, cfg.sheet_id)`; `syncer = sheets.SheetSyncer(mirror, conn) if mirror else None`; `rate_limiter = ops.RateLimiter(max_per_window=8, window_seconds=3600, min_interval_seconds=10)`; pass into `build_application(..., sheet_syncer=syncer, rate_limiter=rate_limiter)`; set `app.bot_data["db_path"]=cfg.db_path`. Extend `config.Config` + `load_config` with `google_service_account_json` (`GOOGLE_SERVICE_ACCOUNT_JSON`) and `sheet_id` (`SHEET_ID`), both optional.

- [ ] **Step 9 — `requirements.txt`:** add `gspread>=6.0` and `google-auth>=2.0`. **`.env.example`:** add `GOOGLE_SERVICE_ACCOUNT_JSON=` and `SHEET_ID=` with comments.

- [ ] **Step 10 — Update affected existing tests:** `tests/test_bot.py` `build_application` calls now must tolerate new optional kwargs (they default to None). Add a test that `on_receipt` is blocked by a saturated rate limiter, and that `on_sutd_id` taken-branch writes a relink request.

- [ ] **Step 11 — Full suite green.** `\.venv\Scripts\python -m pytest -q`.

- [ ] **Step 12 — Commit** `feat: wire admin/settings/sheets/ops into the application + jobs`.

---

### Task 9: Go-live guide + doc updates

**Files:**
- Create: `DEPLOY.md`
- Modify: `README.md`, `CLAUDE.md`, `MEMORY.md`, `how does this bot work.md` (command list)

- [ ] **Step 1 — `DEPLOY.md`** per spec §8 (Oracle Always Free VM, Python 3.12, venv, `.env` incl. paid Gemini key + service account + sheet id, Google service-account creation + sheet sharing, systemd unit with `Restart=on-failure`, journalctl, backups dir, restore steps, pre-launch checklist: wipe test `clubbot.db`, register 2–3 real members, `/newterm <name> 20.00 <start> <end>`, one real S$20 E2E before club-wide).
- [ ] **Step 2 — README/CLAUDE/how-does-this-work:** document new commands (`/addadmin /removeadmin /transfertreasurer /relink /settings /sync /backup`) and link `DEPLOY.md`. Add a CLAUDE.md ground rule: routing-critical settings changes require confirmation; back up the `backups/` dir too.
- [ ] **Step 3 — `MEMORY.md`:** new dated entry summarizing Phase 4 (what shipped, schema additions, test count, what remains: live deploy + first real term).
- [ ] **Step 4 — Commit** `docs: deploy guide and Phase 4 documentation`.

---

## Self-review notes (coverage map)

- Spec §1 → Task 1. §2 → Tasks 2,6. §3 → Tasks 2,6,8(4). §4 → Tasks 3,5,8(2). §5 → Tasks 7,8(5,6,7). §6 → Tasks 2(WAL),4,8(3),8(7). §7 → Task 2. §8 → Task 9. §9/§10 testing → every task is TDD; full-suite gate in Tasks 1,3,5,6,7,8.
- Cross-task names verified: `PayNowConfig(uen,merchant_name,bill_number,recipient_match)`, `get_paynow_config`, `db.add_admin/remove_admin/transfer_treasurer/upsert_relink_request/get_relink_request/delete_relink_request/reassign_member_telegram_id/list_all_payments`, `ops.RateLimiter(max_per_window,window_seconds,min_interval_seconds,clock)/chunk_text/reply_long/backup_database/on_error/mark_dirty`, `sheets.SheetMirror/SheetSyncer/member_rows/payment_rows/create_mirror_from_env` — all consistent.
