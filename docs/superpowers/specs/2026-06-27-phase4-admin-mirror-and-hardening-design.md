# Phase 4 — Admin Management, Sheet Mirror & Launch Hardening

**Design Document / Spec** · 2026-06-27 · Status: approved by treasurer

This spec completes the SUTD Badminton Club bot for production launch. It builds the
remaining Phase 4 commands (PRD §10), adds the Google Sheet mirror (PRD §7.6), and a
robustness/security layer so the bot degrades gracefully and never crashes a member's
flow. It also fixes a launch-blocking test regression.

**Reads:** PRD `docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`,
`MEMORY.md`. Phases 0–3 are complete and proven live.

## Guiding principle

Every new external dependency (Google Sheets, Gemini, Telegram sends) is **optional,
wrapped, and isolated**. A failure in any of them is logged and swallowed — it never
breaks registration, `/pay`, receipt verification, or an admin command. Deterministic
membership decisions remain in code (PRD §7.3); the VLM only extracts.

## Confirmed launch decisions

- Real SUTD student IDs **start with `1010`** (7 digits). The validator
  (`validation.normalize_sutd_id`, regex `1010\d{3}`) is correct; the failing tests
  carry stale fixtures and will be fixed to match.
- **Google Sheet mirror ships in this launch.**
- **Hosting:** free cloud Linux VM (Oracle Always Free recommended; GCP e2-micro alt),
  run under systemd. A `DEPLOY.md` go-live guide is part of this work.
- **Gemini:** billing-enabled (paid) key for privacy. No code change; covered in
  `DEPLOY.md`.

---

## 1. Launch blocker — fix the red test suite

Currently 3 of 107 tests fail because fixtures use IDs like `1007654` that the
`1010`-prefixed validator rejects. Fix:

- `tests/test_validation.py::test_sutd_id_accepts_seven_digits` — use a `1010xxx` ID.
- `tests/test_bot.py::test_full_registration_flow`,
  `test_duplicate_sutd_id_blocked` — use `1010xxx` IDs.

Also harden registration: `bot.on_confirm` wraps `db.add_member` in `try/except
sqlite3.IntegrityError` so a duplicate-SUTD race (two accounts confirming the same ID)
yields a friendly "already registered" message instead of an unhandled exception.

**Done when:** `pytest` is fully green before any feature work begins.

---

## 2. Admin management — `clubbot/admin_manage.py`

New, self-contained handler module. All commands treasurer-only; all reuse the
existing `_resolve_member` (SUTD-ID lookup) pattern from `admin.py`.

### `/addadmin <sutd_id>`
- Target must be a registered member. Reject if already an admin/treasurer.
- Insert `admins(telegram_user_id, role='admin', added_by=<treasurer>)`.
- Confirm to treasurer; DM the new admin that they have admin access.

### `/removeadmin <sutd_id>`
- Remove a row only when its role is `admin`. Never remove the treasurer
  (reply with a clear refusal). No-op-safe if not an admin.

### `/transfertreasurer <sutd_id>` (two-step confirm)
- Target must be a registered member and not already treasurer.
- Show an inline **Confirm / Cancel** keyboard. Pending action stashed in
  `context.bot_data["pending_transfer"][treasurer_id]`.
- On confirm (re-check the caller is the current treasurer): in one transaction,
  promote target to `treasurer` and demote the previous treasurer to `admin`.
  Exactly one `treasurer` row always exists.
- DM both parties. The bootstrap in `db.ensure_treasurer` already yields to an
  existing treasurer row, so `.env` cannot silently override the handoff.

### New `db` queries
`add_admin`, `remove_admin`, `list_admins`, `transfer_treasurer` (atomic
promote+demote), and a guard helper `is_admin_role`.

---

## 3. `/relink <sutd_id>` — member changed Telegram account (`admin_manage.py`)

A member who lost/changed their Telegram account cannot re-register (SUTD ID is
taken). The treasurer relinks the SUTD ID to the new account, **preserving all
payment history**.

### Pending-request capture
When a new account enters an already-registered SUTD ID during `/start`
(`bot.on_sutd_id`, the `SUTD_ID_TAKEN` branch), record a request:
`relink_requests(sutd_id, new_telegram_user_id, new_username, requested_at)` (upsert
on `sutd_id`). The member is told to ask the treasurer to relink.

### Treasurer flow
- `/relink <sutd_id>` — if a pending request exists, show
  "move SUTD `<id>` from @old (`old_id`) → @new (`new_id`)" with **Confirm / Cancel**.
- `/relink <sutd_id> <new_telegram_id>` — explicit/proactive form, same confirm.
- Guards: SUTD ID must exist; new id must not already belong to a different member;
  new id must differ from old.

### Atomic reassignment — `db.reassign_member_telegram_id(conn, old_id, new_id, new_username)`
Changing the member PK while children reference it is FK-sensitive. Implementation:
`conn.commit()` to close any open tx → `PRAGMA foreign_keys=OFF` → in one transaction
`UPDATE members`, `UPDATE payments SET member_id`, `UPDATE admins` (in case the member
was an admin) → `commit` → `PRAGMA foreign_keys=ON`. Then delete the consumed
`relink_requests` row. Covered by a test that asserts payment history follows the
member to the new id and FK integrity holds.

### New `db` queries
`upsert_relink_request`, `get_relink_request`, `delete_relink_request`,
`reassign_member_telegram_id`.

---

## 4. `/settings` — settings-backed PayNow config

PRD §12: the school account/QR fields must be editable without code changes.

### `clubbot/paynow_config.py`
- `PayNowConfig` dataclass: `uen`, `merchant_name`, `bill_number`, `recipient_match`.
- `get_paynow_config(conn)` reads `settings` keys (`paynow_uen`, `merchant_name`,
  `bill_number`, `recipient_match`), each defaulting to the current
  `payments.SCHOOL_*` constant / `"SINGAPOREUNIVERSITYOF"`.
- `EDITABLE_KEYS` whitelist; `ROUTING_CRITICAL = {"paynow_uen", "bill_number"}`.
- `set_value(conn, key, value)` validates key membership and non-empty value.

### `payments.py` refactor (backward-compatible)
- `build_member_qr(*, fee_cents, reference, config: PayNowConfig | None = None)` —
  `None` uses the module constants, so all existing call sites/tests still pass.
- `verify_extracted_payment(..., config: PayNowConfig | None = None)` — bill-number
  and recipient checks read from `config` when provided, else the constants.
- Call sites (`bot.cmd_pay`, `bot.on_receipt`, `scheduler.do_term_start_blast`) pass
  `get_paynow_config(conn)`.

### `/settings` command (in `paynow_config` handler section or `admin.py`)
- No args → show current effective config (any reader = admin).
- `/settings set <key> <value>` → treasurer-only. For `ROUTING_CRITICAL` keys, show a
  **Confirm / Cancel** keyboard with a Phase-0 warning ("changing this can stop
  payments reaching DBS FLYMAX"); pending change stashed in `bot_data`. Non-critical
  keys apply immediately.

---

## 5. Google Sheet mirror — `clubbot/sheets.py`

One-way bot→Sheet, read-only for exco. Never the source of truth.

### Components
- `SheetMirror` — adapter over `gspread` + a service account. Builds two worksheets:
  **Members** (`full_name, sutd_id, username, telegram_user_id, active, joined_at`) and
  **Payments** (`member, sutd_id, term, status, amount, verified_by, payment_time,
  flagged, audit_confirmed`). `full_rebuild(conn)` clears + rewrites both tabs from the
  DB. Constructed from `GOOGLE_SERVICE_ACCOUNT_JSON` + `SHEET_ID`; missing/invalid
  config → constructor returns/raises and `__main__` treats the mirror as disabled.
- `SheetSyncer` — coalescing scheduler. `mark_dirty()` schedules a single background
  `full_rebuild` after a short debounce (≈5 s) via `asyncio.create_task` +
  `asyncio.to_thread` (gspread is blocking). Overlapping marks collapse into one
  rebuild. Every rebuild is wrapped: a Sheets/network error is logged and dropped.
- Disabled mode: if no mirror is configured, `mark_dirty()` is a no-op and `/sync`
  replies that the mirror is not configured.

### Hook points (added during integration by the core owner)
`mark_dirty` after: registration confirm, receipt verified, `/markpaid`, `/revoke`,
`/flag`, relink, `/newterm`, term-start blast. Plus a nightly `full_rebuild` job in the
scheduler and a treasurer `/sync` command.

### Tests
gspread fully mocked (no network): assert `full_rebuild` writes the expected rows;
assert `SheetSyncer` coalesces multiple `mark_dirty` calls into one rebuild; assert a
raising mirror does not propagate.

---

## 6. Security & robustness — `clubbot/ops.py`

1. **Global error handler** `on_error(update, context)` — registered via
   `app.add_error_handler`. Logs the full traceback; DMs the treasurer a short,
   **rate-limited** alert (≤1 per N minutes) so a storm of errors can't spam them.
2. **`RateLimiter`** — token/timestamp bucket keyed by user id (monotonic clock,
   in-memory; resets on restart, acceptable). Applied to the Gemini receipt path
   (min interval between submissions + hourly cap) to protect quota/cost and block
   abuse. A light global command throttle guards floods. Over-limit → friendly
   "please wait a moment" reply, no extraction call.
3. **SQLite durability** — `connect()` sets `PRAGMA journal_mode=WAL` and
   `PRAGMA busy_timeout=5000`.
4. **DB backups** — `backup_database(db_path)` uses SQLite's online-backup API
   (consistent copy while running) to `backups/clubbot-YYYYMMDD-HHMMSS.db`, retaining
   the most recent N. Daily scheduler job + treasurer `/backup` command.
5. **`send_long(message, text)`** — splits replies safely under Telegram's 4096-char
   limit; used by `/members`, `/unpaid`, `/audit`.
6. **Defensive callbacks** — every confirm button (`transfer`, `relink`, `settings`)
   re-checks the caller is the treasurer and validates its payload before acting.

`ops.py` is part of the core (written by the integrator) so every module can import its
helpers without a circular dependency.

---

## 7. Data model additions (additive, auto-migrated)

- `relink_requests(sutd_id TEXT PRIMARY KEY, new_telegram_user_id INTEGER NOT NULL,
  new_username TEXT, requested_at TEXT NOT NULL DEFAULT (datetime('now')))`.
- New `settings` keys: `paynow_uen`, `merchant_name`, `bill_number`, `recipient_match`
  (no schema change — `settings` already exists).
- No destructive migrations. `receipt_fingerprints` is never cleared (CLAUDE.md).

---

## 8. Deployment — `DEPLOY.md`

Free cloud Linux VM (Oracle Always Free recommended), step-by-step:

1. Create the VM (Ubuntu LTS), SSH in.
2. Install Python 3.12, git; clone repo; `python -m venv .venv`; install requirements.
3. Create `.env`: `BOT_TOKEN`, `TREASURER_TELEGRAM_ID`, `GEMINI_API_KEY` (billing
   enabled), `GEMINI_MODEL`, `DB_PATH`, and Sheet vars
   (`GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`).
4. Google service account: create in Google Cloud, enable Sheets API, download JSON,
   share the target Sheet with the service-account email as Editor.
5. systemd unit (`clubbot.service`): restart-on-failure, run as a non-root user.
6. Logs via `journalctl -u clubbot -f`; backups dir; how to restore.
7. **Pre-launch checklist:** delete the test `clubbot.db`, start fresh, register 2–3
   real members, open the real term with `/newterm <name> 20.00 <start> <end>`, verify
   one real S$20 payment end-to-end before announcing club-wide.

---

## 9. Build, test & review process

- **Core (integrator):** `db.py` schema + all new queries; `payments.py` config
  refactor; `ops.py`; the 3 test fixes + `on_confirm` hardening; `build_application`
  and `__main__` wiring; `requirements.txt`; `.env.example`; sync hook calls.
- **Three parallel subagents** build disjoint new modules + their own tests:
  `admin_manage.py`, `paynow_config.py`, `sheets.py`.
- Integrate → full `pytest` must be **green** (existing 107 + new tests).
- **Codex reviews the whole codebase** (treasurer's explicit request); fix findings.
- Write `DEPLOY.md`; update `README.md`, `CLAUDE.md` ground rules, `MEMORY.md`.

## 10. Testing

- Unit (pytest), no network: admin role transitions; transfer atomicity; relink
  reassignment preserves payment history + FK integrity; settings whitelist +
  routing-critical confirm; PayNow config defaults + override; Sheet `full_rebuild`
  rows + syncer coalescing + failure isolation; rate-limiter windows; backup creates a
  readable copy; message chunking boundaries; the registration/validation fixes.
- Manual E2E before club-wide launch (per the `DEPLOY.md` checklist).

## 11. Out of scope (YAGNI)

Refunds/money movement; incremental per-cell Sheet sync; member-facing admin UI;
multi-club support; changing the verification model from Gemini.
