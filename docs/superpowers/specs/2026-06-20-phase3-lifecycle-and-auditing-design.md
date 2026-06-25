# Phase 3 — Lifecycle & Auditing (Design)

**Date:** 2026-06-20
**Status:** Approved, building in one push.
**Builds on:** Phase 2 (payment engine). Read `MEMORY.md` and the PRD
(`docs/superpowers/specs/2026-06-11-club-payment-bot-design.md` §9, §13) first.

## Goal

Make the bot do the chasing and bookkeeping so the treasurer's only recurring
task is a ~5–10 min weekly check against DBS FLYMAX. Three behaviours plus a
restart-safe scheduler.

## Treasurer's chosen options

- **Reminders:** gentle — one term-start DM (with QR) + a single day-7 nudge to
  the still-unpaid. No day 3 / 14.
- **Audit:** flag-only. The bot never auto-revokes; the treasurer revokes by hand.
- **Build:** the whole phase at once, kept in clean modules.

## Architecture (follows existing layering)

| File | Responsibility |
|---|---|
| `clubbot/format.py` *(new)* | `money(cents) -> str` shared by `bot.py`, `admin.py`, `scheduler.py` |
| `clubbot/db.py` | New queries + 4 new nullable columns + migration |
| `clubbot/scheduler.py` *(new)* | Pure run-time calculators, restart-safe job restoration, and the three job actions (term-start blast, day-7 reminder, weekly audit digest) |
| `clubbot/admin.py` *(new)* | Treasurer/admin command handlers (thin: permission → db → reply) |
| `clubbot/bot.py` | Register the new handlers; call `scheduler.schedule_all` |
| `requirements.txt` | Pin `python-telegram-bot[job-queue]` |

Pure logic stays out of async handlers so it is unit-testable.

## Schema changes (auto-migrated, like Phase 2)

- `terms`: `start_notified_at TEXT`, `reminder7_sent_at TEXT` — completion stamps
  so a restart never re-sends.
- `payments`: `flagged_at TEXT` (audit flag, no member impact),
  `audit_confirmed_at TEXT` (watermark; each digest shows only unconfirmed).

`_migrate()` adds any missing column; `SCHEMA` includes them for fresh DBs.

## `db.py` contract (exact signatures)

```python
def list_active_members(conn) -> list[Row]            # active=1, ORDER BY full_name
def list_members(conn) -> list[Row]                   # all, ORDER BY full_name
def list_unpaid_members(conn, term_id) -> list[Row]   # active, no verified payment this term
def get_term_payment_stats(conn, term_id) -> dict     # registered, paid, unpaid, exceptions, flagged
def mark_paid_manual(conn, *, member_id, term_id) -> Row   # verified, verified_by='manual_override'
def flag_payment(conn, *, member_id, term_id) -> Row      # set flagged_at; ValueError if no payment
def revoke_payment(conn, *, member_id, term_id) -> Row    # verified -> revoked; ValueError otherwise
def list_unconfirmed_verified_payments(conn) -> list[Row] # status='verified' AND audit_confirmed_at IS NULL
def confirm_payments_audited(conn, payment_ids: list[int]) -> None
def record_audit(conn, *, period_start, period_end, payment_count, result) -> None
def mark_term_start_notified(conn, term_id) -> None
def mark_term_reminder7_sent(conn, term_id) -> None
def list_terms(conn) -> list[Row]                     # ORDER BY start_date
```

`get_payment` already joins member/term fields (full_name, sutd_id,
telegram_user_id, term_name, fee_cents). Reuse it.

## `scheduler.py` contract

Times in Singapore time (`SINGAPORE_TIME` from `clubbot.payments`).
`REMINDER_HOUR = 10`, `AUDIT_HOUR = 9`.

```python
# pure
def term_start_run_time(term, now) -> datetime           # start_date 10:00 SGT, else now
def reminder7_run_time(term) -> datetime                 # (start+7d) 10:00 SGT
def pending_term_jobs(conn, now) -> list[tuple[str, Row, datetime]]
    # ('start', term, when) if start_notified_at is None and term not ended
    # ('reminder7', term, when) if reminder7_sent_at is None and term not ended

# actions (testable with a mock bot + in-memory conn)
async def do_term_start_blast(bot, conn, term_id) -> None   # QR to each active, non-verified member; stamp start_notified_at
async def send_unpaid_reminders(bot, conn, term_id) -> int  # DM unpaid; returns count; no stamp
async def do_reminder7(bot, conn, term_id) -> None          # send_unpaid_reminders + stamp reminder7_sent_at
async def do_audit_digest(bot, conn) -> bool                # DM treasurer unconfirmed verified payments + 'All found' button; False if nothing

# glue
def schedule_all(app, conn) -> None
```

- A member who blocked the bot raises on send → log and continue; never abort a blast.
- Weekly digest: `run_daily` at 09:00 SGT **every day**, the callback returns early
  unless `now.weekday() == 0` (Monday). This avoids PTB's day-index ambiguity.
- One-shot jobs use `run_once(when=max(run_time, now+small), data={'term_id': ...})`.

## `admin.py` contract

Permission helpers: `_is_admin` (treasurer or admin), `_is_treasurer`.

| Command | Who | Action |
|---|---|---|
| `/unpaid` | admin | list active members not verified this term |
| `/stats` | admin | counts from `get_term_payment_stats` |
| `/members` | admin | full member list |
| `/markpaid <sutd_id>` | treasurer | `mark_paid_manual`; reply confirm |
| `/remind` | treasurer | `send_unpaid_reminders` now; reply count |
| `/audit` | treasurer | `do_audit_digest` on demand |
| `/flag <sutd_id>` | treasurer | `flag_payment`; **no** member notification |
| `/revoke <sutd_id>` | treasurer | `revoke_payment` + DM the member |
| `audit:allfound` (button) | treasurer | `confirm_payments_audited` + `record_audit` |

`<sutd_id>` commands resolve the member via `db.get_member_by_sutd_id`; missing
member or missing arg → friendly error.

## Testing

- `test_db.py`: new queries, new-column migration.
- `test_scheduler.py`: time calculators, `pending_term_jobs`, each action with a
  mock bot (assert messages + DB stamps), send-failure resilience.
- `test_admin.py`: every command incl. permission denials and bad input.
- Update `test_build_application_smoke` handler count.

## Out of scope (Phase 4)

`/addadmin`, `/removeadmin`, `/transfertreasurer`, `/relink`, `/settings`,
Google Sheet mirror.
