# SUTD Badminton Club Payment Bot

Telegram bot that runs the club's membership fee collection end to end:
members register, pay a per-term fee via a personal PayNow QR, send back the
bank screenshot, and get verified automatically (Gemini Flash extracts the
fields; deterministic Python code decides). The treasurer only handles rare
exception taps and a weekly audit digest.

**This file is the project's memory.** Read it fully at the start of every
session, and **update the Status and History sections before ending any work
session** — that is how sessions hand off. The full design/PRD is at
`docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`; read it before
changing payment or verification logic.

## Status (2026-07-14)

- **Feature-complete per the PRD (Phases 0–5), reviewed, and hardened.**
  All phases built and unit-tested (137 pytest tests). A full multi-agent +
  manual code review fixed 11 bugs (relink hijack window, normalised
  transaction-ID bypass, edited-message crashes, UTC/SGT term skew,
  restart-safe blasts, Sheet grid growth, and more — see git log `f84460f`).
- **Live-proven:** registration and the full S$0.05 payment flow (QR → real
  payment → screenshot → Gemini → auto-verify) succeeded on real Telegram.
  The bot is named **SUTD ShuttleBuddy** (handle still `@MyClubFinanceBot`;
  renamed 2026-07-14). Scheduled jobs are unit-tested but not yet observed
  over a real multi-day term.
- **A version is deployed and running on the treasurer's Raspberry Pi 4**
  as of 2026-07-14. Pi layout: user `blud`, host `blud.local`, code at
  `~/clubbot`, systemd service `clubbot` installed by `deploy/setup_pi.sh`
  (idempotent — re-run it after every code copy). Update flow = scp the
  code + secrets from this folder, then re-run the script (README has the
  exact commands).
- **Beware a stale parallel copy:** the Pi was first deployed from a second
  working copy at `Documents\SUTD Projects\Badmintion Tele Bot` (different
  remote, `badminton-tele-bot`, stuck at its first commit — another session
  worked there on 2026-07-13 and wrote the original setup_pi.sh, since
  adopted here). THIS repo (`SUTD-Badminton-Treasury-Automator`) is the
  source of truth; do not work in or deploy from the old folder.
- **Deployment target:** Raspberry Pi 4, systemd (`deploy/clubbot.service`),
  24/7. `scripts/preflight.py` verifies the Telegram/Gemini/Sheet secrets.
- **Last known blockers (treasurer's side, 2026-07-14):** Google Sheet not yet
  shared with the service account (`firebase-adminsdk-fbsvc@clubsync-e7436.iam.gserviceaccount.com`),
  and the Gemini key needed replacing (old project billing-suspended). Rerun
  preflight to see current state.

### Launch checklist (remaining user actions)

1. Get all three `scripts/preflight.py` lines to PASS.
2. Wipe test data before real launch: stop the bot, delete `clubbot.db`,
   restart. (Acceptable only this once, before the first real term ever
   opens — `receipt_fingerprints` must never be cleared after that.)
3. Deploy to the Pi per README; enable the backup cron.
4. Live-smoke the admin commands (`/settings`, `/addadmin`, `/relink` with a
   second account, `/transfertreasurer` and back).
5. Open the first real term: `/newterm <name> 20.00 <start> <end>`.
6. Watch the first term: term-start blast and day-7 nudge fire on schedule;
   a mid-term reboot must not re-blast (stamps + per-member `qr_issued_at`
   prevent it).

## Ground rules

- The user is the club treasurer, not a professional developer — explain
  choices plainly, ask before scope changes.
- Never store payment screenshots on disk; keep Telegram file_id + extracted
  fields only. Secrets go in `.env` / `service-account.json` (both
  gitignored), never in code.
- The VLM (Gemini Flash) only *extracts* data from screenshots; membership
  decisions are made by deterministic checks in code (PRD §7.3).
- Members are keyed by Telegram user ID, never by @username.
- The school account is called **DBS FLYMAX**. The treasurer has view-only
  app access: no API, no export, no alerts — hence screenshot verification
  plus a weekly human audit digest.
- Keep fees configurable per term; never hardcode a fee amount.
- `receipt_fingerprints` is permanent anti-reuse history. Never clear it
  between terms, and back up `clubbot.db`.

## Hard-won facts (do not re-litigate)

- **Phase 0 experiment (2026-06-20):** every QR must preserve the school's
  original Billing ID `200913519CSL5EIU616138169` (UEN `200913519CSL5`) — a
  test payment with a replaced bill number never cleared into the club
  account. The QR's extra reference label is NOT visible in FLYMAX or on
  payer receipts.
- **Payer receipts do not show the QR's `BDM...` reference**, so verification
  instead checks: success screen, exact amount, recipient matches SUTD,
  exact Billing ID, timezone-aware payment time (inside term, not future,
  not before that member's first `/pay`), globally unique image SHA-256, and
  globally unique normalised bank reference (own-payment retries allowed).
- **Accepted residual risk:** a receipt shows no member identity, so two
  colluding members could swap one unused receipt; the second still needs a
  valid payment, and the weekly FLYMAX audit (payer name/amount/date) is the
  backstop. The treasurer accepted this.
- All date/time logic uses explicit Singapore time (`db.SINGAPORE_TIME`);
  the host OS timezone (UTC on the Pi) must not matter.
- SUTD IDs: 7 digits starting `1010`.
- Treasurer bootstraps from `.env` only when the DB has none; after
  `/transfertreasurer` the DB wins.

## Code map

| File | What it is |
|---|---|
| `clubbot/bot.py` | Telegram wiring: registration conversation, /pay, receipt intake, review buttons, edited-message guard |
| `clubbot/admin.py` | All admin/treasurer commands |
| `clubbot/payments.py` | QR building + the deterministic verification rules; `SchoolConfig` (settings-overridable school values) |
| `clubbot/paynow.py` | EMVCo/PayNow TLV payload builder/parser + CRC-16 (golden vector in `tests/test_paynow.py`) |
| `clubbot/qrgen.py` | payload → PNG |
| `clubbot/gemini.py` | Gemini Flash structured extraction (swappable adapter) |
| `clubbot/db.py` | SQLite schema + auto-migration + every query; relink; SGT source of truth |
| `clubbot/scheduler.py` | Term-start blast, day-7 nudge, Monday audit digest, daily self-heal re-arm, Sheet sync jobs |
| `clubbot/sheets.py` | Read-only Google Sheet mirror (Members + Payments tabs) |
| `clubbot/config.py`, `__main__.py` | `.env` loading; entry point `python -m clubbot` |
| `scripts/preflight.py` | Pre-launch connectivity check for all three secrets |
| `deploy/clubbot.service` | systemd unit (Restart=always) |

Stack: Python 3.12+ · python-telegram-bot v21+ (long-polling, JobQueue) ·
SQLite · google-genai (Gemini Flash) · gspread · qrcode · pytest.
Tests: `python -m pytest` (needs `requirements-dev.txt`).

## User context & preferences

- Treasurer; email clubsync26@gmail.com; non-expert builder.
- Wants zero recurring manual work beyond exception taps + the weekly digest.
- Prefers Gemini (free tier) for the VLM; free-vs-paid decision still open.
- Google side lives in the `clubsync-e7436` Cloud/Firebase project.

## History (condensed)

- **2026-06-11** — PRD approved; Phase 0 tooling + Phase 1 core bot built.
- **2026-06-12** — Live on real Telegram; registration works end to end.
- **2026-06-20** — Phase 0 payments proved the Billing ID must be preserved.
  Phase 2 (payment engine) built, redesigned around what receipts actually
  show, and proven live with a real auto-verified S$0.05 payment. Phase 3
  (terms, reminders, audit digest, admin commands) built the same day.
- **2026-07-13** — Phase 4 (`/addadmin` `/removeadmin` `/transfertreasurer`
  `/relink` `/settings`, Google Sheet mirror) + Phase 5 ship artifacts.
- **2026-07-14** — Full-codebase review: 11 bugs fixed (see git log).
  Pi 4 deployment prep, preflight script, repo cleanup: MEMORY.md merged
  into this file; Phase 0 tooling, planning docs, and `image.png` (the
  school's original QR — its decoded payload lives in the PRD §4 and
  `tests/test_paynow.py`) removed. Full git history retains everything.
- **Out of scope for now:** per-transaction bank email alerts (would upgrade
  verification to bank-confirmed; asked of SUTD finance, pending).
