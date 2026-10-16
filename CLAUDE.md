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

## Status (2026-10-16)

- **Codex review (`gpt-6.1-sol`) of the working tree + `c7786c2`/`33839d8`:
  6 findings, all fixed locally (not committed, pushed or deployed).**
  `/reconcile` (`payments.reconcile`): a truncated re-read (`ALICE` vs
  `ALICE TAN`) of one transfer is now the same transfer (fuller name kept);
  an ID match must also agree on time (±15 min); a receipt whose IDs hit two
  transfers is flagged, leaving those transfers free; when a confirmed
  payment and a new receipt both fit one transfer, BOTH are flagged and the
  earlier confirmation is suspended (`result.suspended`). New table
  `bank_line_claims` (txn_id → payment_id, auto-created): every FLYMAX
  transfer that ever confirmed a payment is that payment's permanently (never
  cleared, like `receipt_fingerprints`; one payment can hold several across
  revoke/resubmit), so it can never back another receipt; it re-backs its
  owner only if the owner's current receipt still fits. `/reconcile` saves via
  one all-or-nothing `db.record_reconcile`. `db`: revoke and a new receipt
  (`mark_payment_pending`) clear `audit_confirmed_at`/`flagged_at`, and a new
  receipt also clears the old one's amount/time/settled shirt (the member's
  shirt choice stays). README first-time-setup copies the key from either old
  location and fixes the `.env` path. Also: Sheet "Bank" column renamed "Bank
  status" (Payments + Shirts) and a "TOTAL verified" row added under Payments.
- **Second Codex review (`gpt-6.1-sol`, FULL codebase, 2026-10-16): 9
  findings, all fixed locally with a test each; 334 tests pass. Not
  committed/deployed.** (1) `/relink` now needs a single-use secret link
  (`t.me/<bot>?start=relink_<code>`, `db.arm_relink` returns the code) — a
  SUTD ID alone can no longer take over an account/treasurer role. (2–3) see
  `bank_line_claims` / `record_reconcile` above. (4) the rec-chat sweep
  re-checks payment/Keep/mode/chat right before each ban. (5) `/setgroup`
  refuses one chat as both rec and comp, and `removal._rec_chat` returns None
  if they match (fail closed). (6) receipt-review buttons carry the receipt's
  image-hash prefix; a replaced receipt's old buttons are refused (pre-deploy
  3-part buttons are refused too). (7) see `mark_payment_pending` above. (8)
  a same-day term-start blast now only skips today's reminder for members it
  reached (per-recipient stamps), and reminders wait while the blast retries.
  (9) a join after the noon pick fills an empty nets/backup role and is
  announced. Session duty messages were also reformatted (bold headings,
  date line, "Still collecting"/"New backup"), and the backup's DM now has
  its own "I can't make it" button. **Treasurer rule (2026-10-16, not
  negotiable): once the nets/shuttles job has been passed past the last
  signed-up person (everyone with the role dropped out and nobody is left),
  the session is auto-cancelled** (`sessions._cancel_nobody_left`: DB cancel
  before any await, group post, card shows CANCELLED, host DM; the hourly
  job also cancels any picked session left with nobody). Before the noon pick
  an empty session is NOT cancelled (host DM + hourly check, as before).
  335 tests pass.
- **Third (final) Codex review, 2026-10-16: every pivotal file read in full
  with a per-file verdict.** OK: paynow, qrgen, gemini, db, newterm,
  scheduler, removal, sheets, config, `__main__`, all deploy files,
  requirements. 6 findings, all fixed with tests (342 pass): (1) a FLYMAX
  row read WITHOUT its TXN ID never confirms anyone (FLYMAX always shows one;
  such rows only count for the covered period and are listed in the report
  as "couldn't read the TXN ID"); (2) It's fine / Revoke / /revoke-Confirm
  buttons carry `db.payment_version` (verified_at stamp) and refuse a
  re-verified replacement; (3) a receipt whose IDs hit 2+ transfers is
  contested even when one is already its own; (4) a failed member DM no
  longer blocks the treasurer's review message, and undelivered review
  messages are re-sent by the 15-min retry job (`payments.review_notified_at`;
  the 2 old test-term exceptions will be re-sent once with working buttons);
  (5) the noon duty post re-posts if a Leave/Join changes duties mid-send,
  and is stamped only when still true; (6) cancellations (auto and host) are
  stamped `sessions.cancel_posted_at` only once the group post lands and the
  hourly job retries; it also cancels any picked session left with nobody
  (announced or not). Migration from the committed schema verified.
- **FLYMAX confirmed to be DBS MAX; real screenshot format now known.**
  Treasurer sent two real screenshots (kept locally only, gitignored, in
  `docs/research/flymax-samples/` — real member names/amounts). History tab
  (not Overview) is the row-by-row list `/reconcile` needs: grouped by date,
  each row has payer name, amount, a 20-digit `TXN ID:`, and
  "<collector> collected at <time>". **No Billing ID is shown per row** —
  corrects a PRD §4 assumption; no code change needed since `BankLine`
  already only reads amount/time/name/transaction_id. Still open: whether
  FLYMAX's 20-digit TXN ID equals the reference on a payer's own receipt
  (the UOB payer-receipt IDs on file are 16 digits — a different length, a
  signal but not proof). Full detail and the confirmed layout:
  `docs/research/2026-10-11-fake-receipt-defences.md` §7.
- **Root directory reorganized (local only, not pushed/deployed).** Root now
  holds only folders plus `.env`, `README.md`, `CLAUDE.md` (and the two
  dotfiles git requires at the top level, `.gitignore`/`.gitattributes`).
  Moved into a new `config/` folder: `requirements.txt`,
  `requirements-dev.txt`, `.env.example`, `service-account.json`. Deleted
  `pytest.ini` (folded into running tests as `python -m pytest tests/`
  everywhere instead). Deleted the stale duplicate `AGENTS.md` (superseded by
  this file). Updated every reference: `.env`'s `GOOGLE_SERVICE_ACCOUNT_FILE`,
  `clubbot/config.py`'s error message, `deploy/setup_pi.sh`,
  `deploy/update.sh`, `README.md` (including a one-time Pi migration note for
  `service-account.json`'s new location — **the Pi itself still has it at the
  old top-level path until that migration step is run post-deploy**). 317
  tests still pass.
- **Missing transaction ID no longer blocks auto-verify (not yet deployed).**
  A real test payment from a newly registered member (Clarice Lim, DBS/PayLah-
  style collapsed success screen) failed auto-verify solely because the bank
  reference number sits behind a "show details" chevron most payers never
  tap — Gemini correctly read the screen and returned `transaction_id: null`;
  the deterministic check then hard-failed it to manual review. Since most
  banks behave this way, requiring a transaction ID would send most
  legitimate payments to the treasurer, defeating the automation goal.
  Fixed: `verify_extracted_payment` (`clubbot/payments.py`) no longer treats
  a missing transaction ID as a block — only a transaction ID that IS present
  and already used by another payment still blocks. A missing one now relies
  on `/reconcile`'s own backstop (amount + time ±15 min + a shared name word
  against the treasurer's FLYMAX list; a bank line fitting two payments
  confirms neither). 317 tests updated/pass (`tests/test_payments.py`,
  `tests/test_bot.py`). **Needs a Pi restart to go live** — not deployed yet.
- **Live on the Pi at `33839d8`**, test term with 1-cent fees in the "SUTD
  Badminton EXCO 26" test group. A real S$0.01 UOB payment auto-verified.
- **Fake receipts: `/reconcile` built, Codex-reviewed (5 findings fixed),
  pulled onto the Pi; needs a restart, then a real FLYMAX test.** Key rule
  from the review: a bank line that fits two payments (incl. an
  already-confirmed one) confirms neither, and goes to the treasurer as
  "ambiguous"; the school's Billing ID/UEN never count as a bank txn ID;
  Shirts tab totals count only bank-confirmed or cash shirts. An
  AI-generated receipt nearly auto-verified; screenshots can never prove money
  moved (research `docs/research/2026-10-11-fake-receipt-defences.md`). Plan
  (grilled, treasurer approved): `docs/superpowers/specs/2026-10-11-bank-reconciliation-plan.md`.
  Receipt-verified payments now count as paid but "waiting"; the treasurer
  sends FLYMAX list screenshots to `/reconcile`, Gemini reads the rows,
  `payments.reconcile` matches each payment to one bank line (txn ID, else
  amount + ±15 min + a shared name word) and flags the rest with [Revoke] /
  [It's fine]. Never auto-revokes. Reuses the old audit columns
  (`audit_confirmed_at` = bank-confirmed, `flagged_at` = no match). Sheet
  "Bank" column, `/stats` line, nudge in the weekly backup DM, deterrence
  line in the QR caption. 316 tests pass; Gemini read a synthetic FLYMAX list
  correctly. Open: real FLYMAX screenshot format, and whether FLYMAX's txn ID
  equals payer receipt refs (UOB `2610112184655201`, `2610112184752256`).

- **Full-codebase verification and hardening is committed as `9fbc60d` and
  pushed to `main`; not yet deployed.** 293 tests pass; every Python
  file compiles; `pip check` and `git diff --check` pass; live read-only
  preflight passes Telegram, Gemini, and Google Sheets. Fixes cover fail-closed
  receipt settings, safe non-destructive preflight, `NaN` fee handling, Sheet
  sync triggers, complete relinking, large/per-person removal previews,
  warning retries, crash-safe kick/unban recovery, session rate limits and
  notification/handoff retries, and the Pi Python-version guard. A real
  payment receipt and real group kick/session flow still need the launch
  smoke test because those actions affect real money or members.
- When collection opens, the bot posts a one-time payment announcement with
  the deadline and private-chat payment link to each configured group. It
  catches up after downtime and honors `group_posts off`.
- **Recre session sign-ups (replaces Mitup), merged to `main`, not deployed.**
  Plan `docs/superpowers/specs/2026-10-10-session-signups-plan.md` (treasurer
  approved D1–D5), research `docs/research/2026-10-10-session-announcements-rsvp.md`.
  Admins (the vice captain @mikeclh, once `/addadmin`ed) DM `/session` →
  preview → card in the rec group with Join/Leave, live list + waitlist
  (default 30 spots: 4 courts × 6). Noon on the session day (noon the day
  before if it starts by 2pm) the bot picks a primary (nets + shuttles from the
  store room) and a backup, fair rotation by fewest past primaries; DMs both +
  mentions in the group. Leave / "I can't make it" by the primary hands over
  to the backup and picks a new backup. Code review and recovery-path tests
  are complete. Next: deploy, then the vice captain registers and gets
  `/addadmin`.
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
  merged to `main` and pushed 2026-10-10 (`e586358`, together with the session sign-ups), not live-tested. Rec chat ONLY, never the comp
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
  fields only. Secrets go in `.env` (repo root) / `config/service-account.json`
  (both gitignored), never in code.
- The VLM (Gemini Flash) only *extracts* data from screenshots; membership
  decisions are made by deterministic checks in code (PRD §7.3).
- Members are keyed by Telegram user ID, never by @username.
- The school account is called **DBS FLYMAX**, confirmed to be the **DBS MAX**
  app (History tab, not Overview, has the row-by-row list; see
  `docs/research/2026-10-11-fake-receipt-defences.md` §7). The treasurer has
  view-only app access: no API — hence screenshot verification, backed by
  `/reconcile` (treasurer's FLYMAX list screenshots matched line by line). A
  member's receipt is never proof on its own.
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
| `clubbot/reconcile.py` | `/reconcile`: treasurer's FLYMAX screenshots → bank lines → match report with Revoke / It's fine (`rc:` buttons); rules in `payments.reconcile` |
| `clubbot/sessions.py` | `/session` recre sign-up cards (`ss:` buttons), waitlist, noon nets & shuttles pick + handoff |
| `clubbot/sheets.py` | Read-only Google Sheet mirror (Members + Payments + Shirts tabs) |
| `clubbot/config.py`, `__main__.py` | `.env` loading; entry point `python -m clubbot` |
| `scripts/preflight.py` | Pre-launch connectivity check for all three secrets |
| `deploy/clubbot.service` | systemd unit (Restart=always) |
| `deploy/setup_pi.sh`, `deploy/update.sh` | Pi install/restart; update = `git pull` + setup |

Stack: Python 3.12+ · python-telegram-bot v21+ (long-polling, JobQueue) ·
SQLite · google-genai (Gemini Flash) · gspread · qrcode · pytest.
Tests: `python -m pytest tests/` (needs `config/requirements-dev.txt`).

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
  `scripts/import_rec_members.py`. Merged to `main` with the session sign-ups (274 tests pass); not live-tested.
- **2026-10-10** — Recre session sign-ups to replace Mitup for the vice
  captain: research → plan → self-grill (20 questions, waitlist / handoff /
  cancel / noon pick added) → treasurer decisions (admin host, anyone in rec
  group joins, noon pick, fair rotation, 30 spots) → built in
  `clubbot/sessions.py` + `tests/test_sessions.py`. Not yet reviewed or live.
- **2026-10-10** — Full-codebase review and regression pass: fixed receipt
  verification values that normalized empty, destructive Sheet preflight,
  non-finite fees, stale Sheet mirrors, incomplete account relinks, removal
  preview limits and per-member warning windows, crash-safe unban recovery,
  and session notification/card/handoff retry gaps. Added Pi Python 3.12+
  enforcement. 293 tests, compilation, dependency validation, patch checks,
  and all three live preflight connections pass. Real payment/group smoke
  tests remain a launch action.
- **2026-10-10** — Added one-time payment-open posts to configured groups,
  with the deadline and private payment link. Failed deliveries retry and the
  scheduler catches up after downtime; `group_posts off` suppresses them.
- **2026-10-11** — First real v2 payment (UOB TMRW, S$0.20) went to review:
  Gemini returned the UEN as `billing_id` (the prompt said "UEN/bill
  reference"); UOB shows the full Billing ID under "Reference no. (optional)".
  Fix (`495d325`, deployed): Gemini also lists every ID on the
  receipt (`reference_numbers`) and code matches the exact Billing ID among
  them; a transaction ID equal to the school's Billing ID/UEN is treated as
  missing so it can never be reserved and block later payers. Re-run on the
  real receipt: 5/5 verified. 301 tests pass. Other banks (DBS/POSB, OCBC,
  PayLah) still unproven; failures there fail closed to treasurer review.
- **2026-10-14** — Diagnosed a real auto-verify failure on the Pi (SSH over
  Tailscale): a newly registered member's receipt was a bank app's collapsed
  success screen with no visible transaction ID (DBS/PayLah-style; the number
  sits behind an unexpanded chevron). Confirmed by pulling the member's
  receipt off Telegram via the bot's own file_id (viewed once, both the Pi
  and local temp copies deleted immediately after — never kept on disk).
  Gemini extracted correctly; the deterministic "Transaction ID is missing"
  check was too strict for how real banks behave. Fixed per Status above.
- **2026-10-11** — Treasurer's AI-generated fake receipt reached review only
  because its Billing ID was slightly off. Researched (detectors are at chance;
  everyone reconciles against the receiving account), grilled, and built
  `/reconcile` (`clubbot/reconcile.py` + `payments.reconcile`). See Status.
- **Out of scope for now:** per-transaction bank email alerts (would upgrade
  verification to bank-confirmed; asked of SUTD finance, pending).
- **2026-10-16** — Treasurer sent two real FLYMAX screenshots (kept local
  only, gitignored, `docs/research/flymax-samples/`). Confirmed FLYMAX = DBS
  MAX, and documented the real History-tab row format (payer name, amount,
  20-digit TXN ID, collector + time; no per-row Billing ID, correcting a PRD
  §4 assumption). No code change — `BankLine`/`extract_bank_lines` already
  only rely on fields that are actually shown. See
  `docs/research/2026-10-11-fake-receipt-defences.md` §7.
- **2026-10-16** — Sheet: "Bank" → "Bank status", "TOTAL verified" row.
  Codex (`gpt-6.1-sol`) reviewed the uncommitted work + `c7786c2`/`33839d8`:
  4 High + 2 Medium, all in `/reconcile` matching, revoke/resubmit state, and
  the README key migration. All fixed with a test per finding (see Status);
  323 tests pass. Not committed or deployed yet. Then a FULL-codebase Codex
  review with project context: 9 more findings (relink takeover, bank-claim
  history + atomic reconcile writes, stale removal sweep, rec/comp same chat,
  stale review buttons, stale receipt facts, blast vs reminder, session role
  refill), all fixed with tests; session messages reformatted; 334 tests pass.
