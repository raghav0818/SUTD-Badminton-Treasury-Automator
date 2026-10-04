# SUTD Badminton Club Payment Bot

Telegram bot that runs the club's membership fee collection end to end:
members register, pay a per-term fee via a personal PayNow QR, send back the
bank screenshot, and get verified automatically (Gemini Flash extracts the
fields; deterministic Python code decides). The treasurer only handles rare
exception taps and a once-per-term FLYMAX count check.

**This file is the project's memory.** Read it fully at the start of every
session, and **update the Status and History sections before ending any work
session** — that is how sessions hand off. The full design/PRD is at
`docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`; read it before
changing payment or verification logic.

## Status (2026-10-04)

- **v2 is built on branch `v2-build` (not merged to `main`, not pushed, not
  deployed).** It implements every item in
  `docs/superpowers/specs/2026-10-04-v2-build-list.md` (A–D): Gemini
  `gemini-3.1-flash-lite`, git-clone deploy + `deploy/update.sh`, Sunday DB
  backup DM, four prices + shirt/size buttons, `/roster` (competitive =
  SUTD ID on roster), `/stats` per-price counts, Shirts Sheet tab, reminders
  d3/d7/d10/d13 + deadline last call, Mon/Thu group progress posts,
  `/setgroup`, per-term opt-out button, registration gate (club group or
  roster), pay prompt right after registration, Gemini-failure auto-retry
  (15 min × 4, then treasurer review), audit/flag removed, consent line.
  Built by 4 parallel subagents in 2 waves and merged, then a full
  `/code-review` of `main...v2-build`: all 10 findings fixed (commit
  `bc44238`), then a Codex review: all 11 findings fixed; 205 tests pass.
  Member/admin commands only work in private chats (the bot sits in the club
  groups, and must be a group ADMIN for /setgroup, since Telegram only
  guarantees getChatMember lookups for admin bots). Reminders/group posts
  are stamped per recipient after delivery (failures retry hourly that day).
- **v1 (on `main`)** was live-proven on real Telegram (registration + a real
  S$0.05 auto-verified payment). Bot name **SUTD ShuttleBuddy**, handle
  `@MyClubFinanceBot`. Scheduled jobs never observed over a real term.
- **Pi 4 is switched OFF** (nobody was using the bot). Its old `~/clubbot`
  copy is half-updated and root-owned; plan is a fresh `git clone` per the
  README (move the old folder aside, keep its `.env`, which may be the only
  copy of the secrets). Pi: user `blud`, host `blud.local`, service `clubbot`.
- **Beware a stale parallel copy** at `Documents\SUTD Projects\Badmintion Tele Bot`
  (remote `badminton-tele-bot`). THIS repo is the source of truth.
- **Treasurer-side open items:** turn on Gemini billing and put the new key
  in `.env`; share the Google Sheet with the service account
  (`firebase-adminsdk-fbsvc@clubsync-e7436.iam.gserviceaccount.com`).

### Launch checklist (remaining user actions)

1. Merge `v2-build` → `main`, push (repo is public; the Pi pulls from GitHub).
2. Fresh Pi setup per README; all three `scripts/preflight.py` lines PASS.
3. Wipe test data once before the first real term (delete `clubbot.db`) —
   `receipt_fingerprints` must never be cleared after that.
4. Add the bot to both group chats as an ADMIN; `/setgroup rec` and
   `/setgroup comp` in each; paste the competitive team with `/roster`.
5. Open the term:
   `/newterm <name> <start> <end> deadline=YYYY-MM-DD comp=20 rec=25 recshirt=30 shirt=15`
6. Retire the MS Form. Watch the first term: blast, reminders, group posts
   fire once each; a mid-term reboot must not re-send.

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
  plus a once-per-term FLYMAX count check (the weekly audit was dropped in v2).
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
  valid payment, and the per-term FLYMAX count check is the only backstop.
  The treasurer accepted this.
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
| `clubbot/scheduler.py` | Hourly due-check (term-start blast, d3/7/10/13 + last-call reminders, Mon/Thu group posts; `term_events` stamps), Sunday DB backup DM, Gemini-failure retry, Sheet sync jobs |
| `clubbot/sheets.py` | Read-only Google Sheet mirror (Members + Payments + Shirts tabs) |
| `clubbot/config.py`, `__main__.py` | `.env` loading; entry point `python -m clubbot` |
| `scripts/preflight.py` | Pre-launch connectivity check for all three secrets |
| `deploy/clubbot.service` | systemd unit (Restart=always) |
| `deploy/setup_pi.sh`, `deploy/update.sh` | Pi install/restart; update = `git pull` + setup |

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
- **2026-10-03** — Research: `docs/research/2026-10-03-payment-verification-alternatives.md`
  (verdict: stay on a PAID cloud VLM; local OCR not worth it; Google now gates
  `gemini-2.5-flash` to past users, so new keys need a 3.x model ID).
- **2026-10-04** — Grilled the whole project with the treasurer. Outcome is
  `docs/superpowers/specs/2026-10-04-v2-build-list.md` (comp/rec prices + shirts,
  bot fully replaces the MS Form,
  competitive roster, deadline reminders, group progress posts, weekly audit
  DROPPED with a per-term FLYMAX count check instead, git-clone deploy).
  Treasurer confirmed; all of it built the same day on `v2-build` (see Status).
- **Out of scope for now:** per-transaction bank email alerts (would upgrade
  verification to bank-confirmed; asked of SUTD finance, pending).
