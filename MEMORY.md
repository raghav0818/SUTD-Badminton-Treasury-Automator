# Project Memory — SUTD Badminton Club Payment Bot

> **Living status file. Every Claude session MUST read this first and update it
> before the session ends** (what was done, what changed, what's next).

## What this project is

Telegram bot for the SUTD Badminton Club: members register, pay term fees via
PayNow QR, get verified automatically (Gemini Flash reads the payment screenshot,
deterministic code runs 5 checks), and get auto-reminded every term. Treasurer
(the user, club treasurer, only person with read-only access to the school's DBS
Flimax account) gets rare exception pings + a weekly audit digest.

**Full design/PRD:** `docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`
— read it before writing any code. All decisions are logged there (§5).

## Current status (update this every session!)

- **2026-06-12** — **Bot is LIVE on real Telegram.** User filled `.env` and
  registered as a test member (full_name "noobslayer", SUTD ID 1010345 — test
  data; wipe `clubbot.db` before real launch). User feedback handled:
  - Rewrote /status & /start-when-registered text (was vague about next steps;
    now: shows @username, says "nothing to do for now", explains the treasurer
    opens the term and a QR will be DM'd). User picked compact style.
  - Username: was already auto-captured at registration; now also shown in
    /status and re-synced from Telegram on every /start//status (handles
    changes). User declined a "set a username" nudge for members without one.
  - Answered "how do I start fee collection": via /newterm — Phase 3, not built
    yet; Phase 2 before it is still blocked on Phase 0 test payments (below).
  - 38 pytest tests passing (was 33). Restored `.env.example` (user had
    renamed it to `.env` instead of copying).
- **2026-06-11 (later)** — User approved spec ("start building"). Plan written
  (`docs/superpowers/plans/2026-06-11-phase0-tooling-and-phase1-core-bot.md`)
  and **fully implemented: Phase 0 tooling + Phase 1 core bot**, 33 pytest tests
  passing, merged to master.
  - `clubbot/paynow.py` — EMVCo/PayNow payload builder + parser + CRC-16;
    proven by re-encoding the school's QR **byte-identically**.
  - `clubbot/qrgen.py` — payload → PNG (round-trip verified with zxing-cpp).
  - `clubbot/phase0.py` + `scripts/make_phase0_qrs.py` — test QRs generated in
    `phase0_qrs/` (gitignored): variant_A.png (school bill number kept, code
    BDMTEST01 in reference-label subfield), variant_B.png (code BDMTEST02 AS
    the bill number). Both S$0.10, amount locked. **Waiting on treasurer to pay
    both and report what Flimax shows.**
  - `clubbot/db.py` — full PRD §8 schema + member/admin/settings queries.
  - `clubbot/bot.py` — /start registration conversation (name → SUTD ID →
    confirm), /status, /help, /cancel; treasurer bootstrapped from .env.
  - `clubbot/config.py` + `__main__.py` — run with `.venv\Scripts\python -m clubbot`.
  - Bot has NOT been run against real Telegram yet — needs user's BOT_TOKEN
    (@BotFather) + TREASURER_TELEGRAM_ID (@userinfobot) in `.env`.
- **2026-06-11** — Brainstorming + design complete. PRD written and approved.
- Phase 0 partially done: school QR (`image.png`) decoded successfully.
  - PayNow proxy: UEN `200913519CSL5` (SUTD UEN + suffix SL5 → club sub-account)
  - Merchant name: `SINGAPORE UNIVERSITY OF T`
  - Static QR, amount editable, embeds fixed bill number
    `200913519CSL5EIU616138169` — **unknown whether Flimax allocation depends on
    it.** Phase 0 test payments (variant A/B, see PRD §13) must resolve this
    BEFORE building Phase 2 (payment engine).
- No code written yet. No git history before today.

## Key decisions (short form — details in PRD §5)

- Stack: Python + python-telegram-bot v21+ (long-polling) + SQLite + gspread,
  free-tier VM (Oracle Always Free / GCP e2-micro).
- VLM: **Gemini Flash**, free tier first (privacy decision pending at launch),
  behind a swappable `extract_payment_details()` function.
- Verification: VLM extracts JSON → code checks amount/recipient/ref-code/date/
  duplicates. Pass → auto-approve. Fail → treasurer exception queue.
- Weekly audit digest to treasurer during collection season.
- Money flow constraint: must land in school DBS Flimax account (view-only,
  no export/API) → all payment aggregators ruled out.
- Members keyed by Telegram user ID, never username.

## User context & preferences

- User is the club treasurer; email clubsync26@gmail.com; non-expert builder —
  explain technical choices plainly.
- User does NOT want recurring manual work; accepted only: rare exception taps +
  weekly ~5–10 min audit digest.
- User prefers Gemini over Claude for the VLM (free tier).

## Next steps (in order)

1. ~~User: get the bot live for testing~~ ✅ done 2026-06-12 — bot runs and
   registration works end-to-end on real Telegram.
2. **User: Phase 0 test payments** (NOW THE GATE) — pay S$0.10 with `phase0_qrs/variant_A.png`
   and `phase0_qrs/variant_B.png` from a personal bank app, check DBS Flimax:
   (a) both arrived? (b) what text/code shows per payment? (c) variant B still
   allocated to club account? → report back; record outcome HERE and in PRD
   §13/§16. **This unblocks Phase 2.**
3. Write Phase 2+ plan (payment engine: per-member QR, screenshot intake,
   Gemini extraction + 5 checks, exception queue) once Phase 0 outcome known.
4. Pending user actions: ask SUTD finance about email alerts (optional, would
   enable full automation); decide Gemini free vs paid at launch; provide term
   fee + dates when Phase 3 starts.
