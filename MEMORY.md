# Project Memory — SUTD Badminton Club Payment Bot

> **Living status file. Every Claude session MUST read this first and update it
> before the session ends** (what was done, what changed, what's next).

## What this project is

Telegram bot for the SUTD Badminton Club: members register, pay term fees via
PayNow QR, get verified automatically (Gemini Flash reads the payment screenshot,
deterministic code validates the real bank fields and prevents receipt reuse),
and get auto-reminded every term. Treasurer
(the user, club treasurer, only person with read-only access to the school's DBS
FLYMAX account) gets rare exception pings + a weekly audit digest.

**Full design/PRD:** `docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`
— read it before writing any code. All decisions are logged there (§5).

## Current status (update this every session!)

- **2026-06-27 — Phase 4 (admin mgmt, Sheet mirror, launch hardening) built.
  172 pytest tests pass. Branch `phase4-launch` (not yet merged to main).**

  Spec: `docs/superpowers/specs/2026-06-27-phase4-admin-mirror-and-hardening-design.md`.
  Plan: `docs/superpowers/plans/2026-06-27-phase4-launch.md`. Built core spine
  inline + three leaf modules by parallel subagents; Codex reviewed twice and its
  findings were applied.

  **New modules**
  - `clubbot/admin_manage.py` — treasurer-only `/addadmin`, `/removeadmin`,
    `/transfertreasurer` (two-step confirm; promotes target, demotes old treasurer
    to admin), `/relink <sutd_id> [new_id]` (two-step confirm; reassigns the member
    PK + payment history to a new Telegram account). Confirms via inline buttons.
  - `clubbot/paynow_config.py` — settings-backed `PayNowConfig` (uen, merchant_name,
    bill_number, recipient_match) defaulting to the verified `payments.SCHOOL_*`
    constants. `/settings` shows config; `/settings set <key> <value>` is
    treasurer-only and requires Confirm for routing-critical keys (paynow_uen,
    bill_number). `set_value` rejects values that can't build a valid PayNow payload.
    QR build + verification read the effective config so they always agree.
  - `clubbot/sheets.py` — read-only Google Sheet mirror (gspread). `SheetMirror`
    full-rebuilds Members+Payments tabs; `SheetSyncer` coalesces rapid changes into
    one debounced rebuild on its OWN per-rebuild sqlite connection (worker thread),
    failure-isolated. Disabled cleanly if unconfigured (`create_mirror_from_env`→None).
  - `clubbot/ops.py` — robustness: global `on_error` (logs + rate-limited treasurer
    DM), `RateLimiter` (per-user receipt throttle), `backup_database` (SQLite online
    backup, keeps 14), `chunk_text`/`reply_long` (4096-char split), `mark_dirty`.

  **Schema/db (auto-migrated, additive)**
  - New `relink_requests(sutd_id PK, new_telegram_user_id, new_username, requested_at)`.
  - `connect()` sets WAL + busy_timeout. New `idx_one_treasurer` partial unique index
    is created in `_migrate` AFTER a dedup pass (so an older 2-treasurer DB self-heals,
    not crashes).
  - New queries: add_admin/remove_admin/list_admins/transfer_treasurer (atomic),
    upsert/get/delete_relink_request, reassign_member_telegram_id (FK-off swap +
    foreign_key_check before commit), list_all_payments.

  **Integration (clubbot/bot.py, admin.py, scheduler.py, config.py, __main__.py)**
  - Registered all new command + confirm-callback handlers; added `app.add_error_handler`.
  - QR build (`/pay`, term-start blast) and receipt verification pass the effective
    PayNow config. Receipt path is rate-limited before any Gemini call.
  - Registration's "SUTD taken" branch records a relink request. `/members` `/unpaid`
    use `reply_long`. Sheet `mark_dirty` after register/verify/exception/markpaid/
    flag/revoke/approve/reject/relink/newterm. New daily jobs: backup (03:00 SGT),
    Sheet rebuild (04:00 SGT). New env: `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`.
    New deps: `gspread`, `google-auth`.

  **Also fixed:** the 3 red tests from a too-strict SUTD-ID validator — real IDs DO
  start with `1010` (treasurer confirmed); fixtures corrected, validator unchanged.
  `on_confirm` now catches a duplicate-SUTD IntegrityError race.

  **Not yet done / next**
  - `DEPLOY.md` written (Oracle Always Free + systemd). Gemini decision: **paid key**.
  - Branch `phase4-launch` not merged to main and not yet run on real Telegram.
  - Treasurer still to: provide real term fee/dates, do the pre-launch checklist in
    DEPLOY.md §12 (wipe test `clubbot.db`, register 2-3 real members, one real S$20
    E2E), and set up the Google service account if using the Sheet mirror.

- **2026-06-20 (later) — Phase 3 (lifecycle & auditing) built. 107 pytest tests pass.**

  Implemented in one push, kept in clean modules that follow the existing
  layering. Treasurer's chosen options: gentle reminders (term-start + a single
  day-7 nudge, no day 3/14), flag-only audit (the bot never auto-revokes), built
  all at once.

  **New files**
  - `clubbot/scheduler.py` — restart-safe jobs on PTB JobQueue:
    - Term-start blast: at `start_date` 10:00 SGT, DMs every active, not-yet-verified
      member their personal QR (reuses the `/pay` QR; sets `qr_issued_at`). Stamps
      `terms.start_notified_at` so a restart never re-blasts.
    - Day-7 nudge: at `start+7d` 10:00 SGT, DMs only the still-unpaid. Stamps
      `terms.reminder7_sent_at`.
    - Weekly audit digest: `run_daily` at 09:00 SGT, gated to Mondays inside the
      callback (`weekday()==0`) to avoid PTB day-index ambiguity. Sends only if
      there are unconfirmed verified payments.
    - A blocked member raises on send → logged and skipped; never aborts a blast.
    - Pure time calculators + `pending_term_jobs()` drive `schedule_all(app, conn)`,
      called from `build_application` (guarded if JobQueue is missing).
    - `schedule_term_jobs(app, conn, term_id)` arms a single term's jobs the moment
      `/newterm` runs, so a term created while the bot is already running gets its
      blast/reminder without needing a restart. Idempotent (each named job replaces
      its prior self). Added after the initial build; brought the suite to 107.
  - `clubbot/admin.py` — treasurer/admin command handlers (thin: permission → db →
    reply). `/unpaid`, `/stats`, `/members` (admin); `/markpaid <sutd_id>`,
    `/remind`, `/audit`, `/flag <sutd_id>`, `/revoke <sutd_id>` (treasurer); plus
    the `audit:allfound` button handler.
  - `clubbot/format.py` — shared `money(cents)` (replaced `bot._money`).

  **Schema (auto-migrated, like Phase 2)**
  - `terms`: `start_notified_at`, `reminder7_sent_at` (job idempotency stamps).
  - `payments`: `flagged_at` (audit flag, no member impact), `audit_confirmed_at`
    (watermark; each digest shows only unconfirmed verified payments).
  - New `db` queries: list_members/active/unpaid, get_term_payment_stats,
    mark_paid_manual (verified_by='manual_override'), flag_payment, revoke_payment
    (verified→revoked only), list_unconfirmed_verified_payments,
    confirm_payments_audited, record_audit, term-stamp setters, get_treasurer_id.

  **Audit flow (flag-only, per treasurer's choice)**
  - Weekly/`/audit` digest lists verified-but-unconfirmed payments + an "All found"
    button → stamps `audit_confirmed_at` and logs an `audits` row.
  - `/flag <sutd_id>` records `flagged_at` (visible in `/stats`), no member DM.
  - `/revoke <sutd_id>` is the deliberate manual removal → status `revoked` + DMs
    the member. The bot never revokes on its own.

  **Notes / not yet done**
  - `requirements.txt` now pins `python-telegram-bot[job-queue]` (APScheduler). The
    `.venv` already has it.
  - Scheduled jobs verified by unit tests (mock bot), NOT yet observed firing on a
    live multi-day run. First real term will be the live proof.
  - **Nothing committed to git this session.** All Phase 3 work (new modules,
    schema, the schedule_term_jobs fix, doc) is in the working tree only. Treasurer
    has not yet given the go-ahead to commit.
  - **Not yet hands-on tested on real Telegram.** Suggested test path: install deps,
    run on a throwaway db via `$env:DB_PATH="test.db"; .venv\Scripts\python -m clubbot`,
    then in Telegram: /start, /newterm Test 0.05 (start=yesterday) → expect auto QR,
    /stats, /markpaid <sutd_id>, /audit → "All found", /flag, /revoke, /remind.
  - Design doc: `docs/superpowers/specs/2026-06-20-phase3-lifecycle-and-auditing-design.md`.
  - Out of scope (Phase 4): `/addadmin`, `/removeadmin`, `/transfertreasurer`,
    `/relink`, `/settings`, Google Sheet mirror.

- **2026-06-20 — Phase 0 completed, Phase 2 built, corrected, and proven live.**

  **A. What Phase 0 tested and what was learned**

  The school's original PayNow QR contains two routing values that initially
  looked as though one might be replaceable with a unique member reference:

  - PayNow UEN: `200913519CSL5`
  - School Billing ID: `200913519CSL5EIU616138169`

  Two locked S$0.10 QRs were generated:

  - Variant A preserved the school Billing ID and placed `BDMTEST01` in the
    separate EMVCo reference-label field.
  - Variant B replaced the school Billing ID with `BDMTEST02`.

  The treasurer paid both. Variant A arrived successfully in the club's DBS
  FLYMAX account. Variant B did not clear into the club account. This proved
  that the original Billing ID is part of the school's internal routing and
  must never be replaced. Every real member QR therefore preserves the UEN and
  Billing ID exactly.

  DBS FLYMAX displayed the payer name, amount, transaction ID, and original
  Billing ID. It did not display `BDMTEST01`. At first, the treasurer believed
  the payer's personal banking receipt displayed `BDMTEST01`, so the initial
  Phase 2 design used that code as the link between the member and receipt.

  **B. Initial Phase 2 implementation**

  Phase 2 was built with:

  - `/newterm <name> <fee> <start> <end>` for the treasurer.
  - A configurable term fee stored in SQLite. The initial live test fee is
    S$0.05; S$0.05 and the future S$20.00 fee are not hardcoded.
  - `/pay`, which creates one payment record per member per term and sends a
    locked-amount PayNow QR.
  - The mandatory school UEN and Billing ID in every QR.
  - A unique `BDM...` reference in the QR's secondary reference-label field.
  - Receipt image handling in memory only; screenshots are not saved to disk.
  - Gemini structured extraction followed by deterministic Python checks.
  - Automatic verification for passing receipts and a treasurer
    Approve/Reject exception flow for failed checks.
  - SQLite payment history, extracted receipt fields, transaction IDs, image
    hashes, verification method, and timestamps.

  **C. Problem discovered during the real S$0.05 test**

  The actual payer receipt, saved locally as `screenshot from personal.png`,
  showed:

  - `Successful`
  - S$0.05
  - `SINGAPORE UNIVERSITY OF TECHNOLOGY AND DESIGN`
  - Payment date and time
  - UEN `200913519CSL5`
  - Billing ID `200913519CSL5EIU616138169`
  - A bank-generated reference number

  It did **not** show the QR's `BDM...` reference. Therefore, the first Phase 2
  verifier incorrectly sent a legitimate payment to treasurer review because
  it required a field that the bank receipt does not expose.

  This also established that there is no single member-specific identifier
  visible on both sides:

  - The payer receipt has the bank's reference number but not the `BDM...` code.
  - DBS FLYMAX has payer name and its own transaction details but not the
    `BDM...` code.

  **D. Verification redesign and screenshot-reuse protection**

  The unusable `BDM...` receipt check was removed. The code now verifies fields
  that really exist on the receipt:

  1. The image is readable.
  2. It is a completed/successful payment screen, not a confirmation screen.
  3. The amount exactly matches the active term fee.
  4. The recipient matches SUTD.
  5. The Billing ID exactly matches `200913519CSL5EIU616138169`.
  6. The full payment timestamp is timezone-aware, falls inside the term, is
     not in the future, and is not before the member requested their QR.
  7. The bank reference number has never been used before.
  8. The image fingerprint has never been used before.

  Members must now run `/pay` before submitting a receipt. The first QR issue
  time is stored in `payments.qr_issued_at` and is not moved forward by repeated
  `/pay` calls. This prevents an unseen receipt from a previous term or an old
  payment from being accepted for the current request.

  A permanent `receipt_fingerprints` table was added:

  - `image_hash` is a SHA-256 fingerprint and is globally unique.
  - `bank_txn_id` is normalized by removing spaces/punctuation and is globally
    unique.
  - These records are retained across retries and future terms.
  - An exact copied screenshot fails the image-hash check.
  - A cropped, recompressed, or otherwise altered copy normally has a different
    image hash, but still fails because its bank reference number is unchanged.
  - A previous receipt never seen by the bot fails if its payment time predates
    the current QR issue.
  - Existing `clubbot.db` files are migrated automatically to add the new
    columns and permanent fingerprint table.

  If Gemini is temporarily unavailable before it can read the bank reference,
  the pending image reservation is released so the member can retry the same
  image later. Once a bank reference has been extracted, its anti-reuse record
  is retained.

  **E. Remaining limitation, explicitly accepted**

  A member could theoretically obtain another member's brand-new, unused
  receipt and submit it first. The bot cannot automatically prove ownership
  because the receipt does not show the Telegram member, SUTD ID, or `BDM...`
  code. The second submission would be rejected as a duplicate.

  This requires deliberate cooperation between two members and does not let one
  payment activate two memberships: the other member would still need another
  valid payment. The treasurer accepted this residual risk. The future weekly
  DBS FLYMAX audit remains the bank-side safeguard and can compare payer name,
  amount, and payment time.

  **F. Live result and verification status**

  After the redesign, the real Telegram/Gemini S$0.05 flow succeeded and the
  bot returned:

  `Payment receipt accepted. / Payment Test / Amount: S$0.05 / Status: Verified`

  This proves the live path works end to end:

  registration → active term → `/pay` → generated PayNow QR → real payment →
  Telegram screenshot → Gemini extraction → deterministic verification →
  SQLite verified status.

  The code compiles, dependencies are consistent, and **59 pytest tests pass**.
  Tests cover QR decoding, Billing ID preservation, S$0.05 amounts, successful
  and failed receipts, exact-image reuse, bank-reference reuse, payment-before-
  QR rejection, treasurer review, and migration of an existing SQLite database.

  Phase 2 is complete. Receipt verification is called verified inside the bot,
  but remains operationally provisional until checked against DBS FLYMAX because
  the bank provides no API, export, webhook, or shared member identifier.
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
    both and report what FLYMAX shows.**
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
    `200913519CSL5EIU616138169` — **unknown whether FLYMAX allocation depends on
    it.** Phase 0 test payments (variant A/B, see PRD §13) must resolve this
    BEFORE building Phase 2 (payment engine).
- Historical note from the start of 2026-06-11: no code or prior git history
  existed at that point. This is superseded by the completed work above.

## Key decisions (short form — details in PRD §5)

- Stack: Python + python-telegram-bot v21+ (long-polling) + SQLite + gspread,
  free-tier VM (Oracle Always Free / GCP e2-micro).
- VLM: **Gemini Flash**, free tier first (privacy decision pending at launch),
  behind a swappable `extract_payment_details()` function.
- Verification: Gemini extracts structured receipt fields only. Deterministic
  code checks successful status, amount, recipient, mandatory Billing ID,
  timezone-aware payment time after QR issue, globally unique image hash, and
  globally unique bank reference. Pass → auto-verify; fail → retry or treasurer
  exception queue. The hidden QR `BDM...` code is not required from receipts.
- Weekly audit digest to treasurer during collection season.
- Money flow constraint: must land in school DBS FLYMAX account (view-only,
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
2. ~~User: Phase 0 test payments~~ ✅ done 2026-06-20 — original school bill
   number is required; the reference-label code is not visible in FLYMAX.
3. ~~Write and implement Phase 2~~ ✅ done 2026-06-20.
   Real S$0.05 Telegram/Gemini payment was auto-verified successfully.
4. ~~Phase 3 — lifecycle and auditing~~ ✅ done 2026-06-20. Term-start blast +
   day-7 reminder, weekly flag-only FLYMAX audit digest, `/unpaid` `/stats`
   `/members` `/markpaid` `/remind` `/audit` `/flag` `/revoke`, restart-safe jobs.
5. ~~Phase 4 — admin management & mirror~~ ✅ done 2026-06-27 (branch
   `phase4-launch`). `/addadmin` `/removeadmin` `/transfertreasurer` `/relink`
   `/settings` `/sync` `/backup`, Google Sheet mirror, and a robustness/security
   layer. Codex-reviewed; 172 tests pass. **Next: merge to main, then deploy per
   `DEPLOY.md` and run the pre-launch checklist on real Telegram.**
6. Watch the first live term: confirm the term-start blast and day-7 nudge fire on
   schedule, and that a reboot mid-term does not re-blast (the `_notified_at`
   stamps should prevent it).
7. Pending user actions: provide the real term fee (S$20.00) + dates to open the
   first real term; decide Gemini free vs paid at launch; ask SUTD finance about
   per-transaction email alerts (optional, would enable full bank confirmation).
