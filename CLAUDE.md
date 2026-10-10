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

## Status (2026-10-07)

- **v2 is merged to `main` and pushed (2026-10-07); not
  yet deployed.** It implements every item in
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
  **v2 has never run on real Telegram** — only the test suite. Next step is
  Pi setup (launch checklist step 2).
- **v1 (on `main`)** was live-proven on real Telegram (registration + a real
  S$0.05 auto-verified payment). Bot name **SUTD ShuttleBuddy**, handle
  `@MyClubFinanceBot`. Scheduled jobs never observed over a real term.
- **Pi 4 (2026-10-07):** OS was reinstalled (no old `~/clubbot`, no old
  service). `blud.local` does not resolve from the laptop; reach it over
  **Tailscale** as `raspberry-pi-4` / `100.117.79.93`, user `blud`
  (`ssh blud@100.117.79.93` works from the laptop via Tailscale SSH; sudo
  needs the treasurer's password). Claude cloned `~/clubbot`, copied `.env`
  + `service-account.json` (chmod 600), built `.venv`; preflight 3/3 PASS on
  the Pi. Treasurer ran `setup_pi.sh`: service `clubbot` is enabled and
  running v2 (first backup DM sent). Deploying later changes: Claude can
  `git pull` + `pip install` over ssh, but the restart needs the
  treasurer (`sudo systemctl restart clubbot`, or `bash ~/clubbot/deploy/update.sh`).
- **Removing unpaid people from the rec chat (2026-10-10)** is built on branch
  `removing-unpaid-members` (plan: `docs/superpowers/specs/removing-member-plan.md`),
  250 tests pass, not merged or live-tested. Rec chat ONLY, never the comp
  chat. Default `remove_unpaid=preview`. Live test needs: bot admin in the rec
  chat with Ban users + Invite users rights, `/setgroup rec` again (makes the
  join-request link), revoke old links, and the one-off
  `scripts/import_rec_members.py` (Telethon; needs api_id/api_hash).
- **Beware a stale parallel copy** at `Documents\SUTD Projects\Badmintion Tele Bot`
  (remote `badminton-tele-bot`). THIS repo is the source of truth.
- **Google side moved (2026-10-07)** to its own Firebase project
  `badminton-club-bot` (clubsync is the treasurer's other app — don't use it).
  Service account `firebase-adminsdk-fbsvc@badminton-club-bot.iam.gserviceaccount.com`;
  Sheet ID `1ftRLbA3kDXmMi5dyukQFgkwhMsv97ACSt7UNLeuUA1o`. On the laptop,
  `.env` + `service-account.json` pass all three preflight checks; copy both
  to the Pi. Open: whether the Gemini key is from the new project, and
  whether billing (Firebase Blaze) is on — the bot works either way.

### Launch checklist (remaining user actions)

1. ~~Merge + push~~ done 2026-10-07.
2. Fresh Pi setup per README; all three `scripts/preflight.py` lines PASS.
3. Smoke test on real Telegram: a short test term, register, one small
   real payment (with and without shirt), `/stats`, a `/setgroup` group post.
4. Wipe the test data once (delete `clubbot.db`) before the first real
   term — `receipt_fingerprints` must never be cleared after that.
5. Add the bot to both group chats as an ADMIN; `/setgroup rec` and
   `/setgroup comp` in each; paste the competitive team with `/roster`.
6. Open the term:
   `/newterm <name> <start> <end> deadline=YYYY-MM-DD comp=20 rec=25 recshirt=30 shirt=15`
7. Retire the MS Form. Watch the first term: blast, reminders, group posts
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
- **A bot cannot list a group's members** (Bot API). It only knows people who
  registered, joined/requested while it watched (`chat_member` needs
  `allowed_updates`), or came from the one-off Telethon import. A kick is
  ban + unban; the join-request link is the only way to DM a stranger
  (5-minute window, DM before approving).
- **v2 design rules settled in review (keep them):** a Telegram `Forbidden`
  (member blocked the bot) counts as delivered; any other send error retries.
  Stamps (`term_events`, `payments.notified_at`) are written only after
  delivery. A payment is a shirt only if it matches the exact with-shirt fee;
  `wants_shirt` (latest choice) is separate from the sticky `shirt_size`.
  `/roster` is all-or-nothing. Relinks pass the same registration gate.
  `/roster` re-prices only payments whose QR has not been issued yet.
- Treasurer bootstraps from `.env` only when the DB has none; after
  `/transfertreasurer` the DB wins.

## Code map

| File | What it is |
|---|---|
| `clubbot/bot.py` | Telegram wiring: registration conversation, /pay, receipt intake, review buttons, edited-message guard |
| `clubbot/admin.py` | All admin/treasurer commands except /newterm; per-role `/` menus; Confirm / tap-list callbacks (`cf:` `mp:` `ra:` `rl:x:`) |
| `clubbot/newterm.py` | `/newterm`: one-line parser + the tap-through wizard (`nt:<key>:<value>` buttons) |
| `clubbot/payments.py` | QR building + the deterministic verification rules; `SchoolConfig` (settings-overridable school values) |
| `clubbot/paynow.py` | EMVCo/PayNow TLV payload builder/parser + CRC-16 (golden vector in `tests/test_paynow.py`) |
| `clubbot/qrgen.py` | payload → PNG |
| `clubbot/gemini.py` | Gemini Flash structured extraction (swappable adapter) |
| `clubbot/db.py` | SQLite schema + auto-migration + every query; relink; SGT source of truth |
| `clubbot/scheduler.py` | Hourly due-check (term-start blast, d3/7/10/13 + last-call reminders, Mon/Thu group posts; `term_events` stamps), Sunday DB backup DM, Gemini-failure retry, Sheet sync jobs |
| `clubbot/removal.py` | Rec chat only: remove unpaid people after deadline + grace (preview/warning/daily sweep), join-request gate, join tracking, `/keep` |
| `scripts/import_rec_members.py` | One-off Telethon import of people already in the rec chat (bots can't list members) |
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
- Google side lives in the `badminton-club-bot` Firebase project (chosen
  over a plain Cloud project by the treasurer).

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
  Treasurer confirmed; all of it built the same day on `v2-build` (see Status)
  by parallel subagents in 2 waves. Then two review passes: `/code-review`
  (10 fixes, `bc44238` — e.g. private info leaking into groups, phantom
  shirts, roster re-pricing issued QRs, orphaned receipt fingerprints, retry
  races, double term-start blast) and a Codex review (11 fixes, `c96237f` —
  relink gate bypass, receipts stuck after a restart, member-DM failure
  blocking the treasurer alert, blast/reminders/group posts stamped before
  delivery, partial `/roster` replace, shirt size from a later `/pay` choice,
  no weekly-backup catch-up, v1 migration not seeding the d7 stamp, Shirts tab
  clearing after a term, `/setgroup` needing the bot to be group admin).
  205 tests pass. Not merged, pushed or live-tested.
- **2026-10-07** — v2 merged to `main` and pushed (`3b693f1`). Google side
  moved off clubsync to Firebase project `badminton-club-bot`; laptop
  preflight 3/3 PASS. Pi set up over Tailscale and running v2. Stopped
  httpx from logging the bot token (`d39147a`). Then "tap instead of type"
  (`docs/superpowers/specs/2026-10-07-easier-commands-plan.md`, research in
  `docs/research/2026-10-07-easier-admin-commands.md`): `/newterm` alone =
  button wizard in `clubbot/newterm.py` (one-time key per question so old
  buttons are refused; nothing created until Create term); per-role `/`
  menus via setMyCommands (synced at startup, on role changes, relink,
  /help); Confirm buttons on /markpaid /revoke /transfertreasurer; Mark-paid
  buttons on /unpaid (≤30); tap lists for /removeadmin and /relink; long
  replies split under 4096 chars; PTB pinned `>=22.8,<23`. Codex review: 6
  findings, all fixed (`80f9a8f`); 231 tests pass. Pulled onto the Pi;
  needs a restart to go live. Real-Telegram test run still not done.
- **2026-10-10** — On branch `removing-unpaid-members`: researched
  (`docs/research/2026-10-10-removing-unpaid-members.md`) and grilled a plan to
  auto-remove unpaid people from the **rec chat only** (never the comp chat):
  `docs/superpowers/specs/removing-member-plan.md` (join-request gate, daily
  sweep after deadline + grace, preview/Keep, one-off Telethon import of
  existing members). Treasurer approved; built the same day: `clubbot/removal.py`,
  `rec_group_people` table, `/keep`, `/settings remove_unpaid|removal_grace_days`,
  `/setgroup rec` rights check + join-request link, `run_polling(allowed_updates=ALL)`,
  `scripts/import_rec_members.py`. 250 tests pass. Not merged or live-tested.
- **Out of scope for now:** per-transaction bank email alerts (would upgrade
  verification to bank-confirmed; asked of SUTD finance, pending).
