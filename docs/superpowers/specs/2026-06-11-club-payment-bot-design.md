# SUTD Badminton Club — Telegram Membership & Payment Bot
**Design Document / PRD** · 2026-06-11 · Status: awaiting user approval

## 1. Problem

The club currently collects term membership fees via Microsoft Forms + a PayNow QR.
Members forget to pay, submissions must be cross-checked by hand, and the exco
spends hours every term chasing people. The treasurer (project owner) is the only
person with (read-only) access to the school's DBS FLYMAX account where fees land.

## 2. Goals

- Members register, pay, and check membership status entirely inside Telegram.
- Payment verification is automatic in the common case (no human in the loop).
- The bot does the chasing: automatic renewal notices and reminders every term.
- Exco can view membership/payment records (bot commands + Google Sheet).
- Treasurer's recurring workload ≈ a weekly 5–10 min audit during collection
  season plus a few one-tap exception approvals per term. Nothing per-payment.
- Survives exco turnover: admins, terms, and treasurer role manageable in-bot.

## 3. Non-goals

- Moving money or refunds (school account is read-only; out of scope).
- Replacing the school's QR/account or integrating with DBS FLYMAX systems
  (no API/export exists; the account is confidential).
- Event signups, court booking, or anything beyond membership + payments (v1).

## 4. Constraints & key facts (verified)

- Money must land in the school's DBS FLYMAX account. Treasurer has **view-only**
  access via app; **no export, no API, no alerts**. Bot can never see the account.
- FLYMAX transaction history shows payer name, amount, date, transaction ID, and
  the fixed school Billing ID. It does not show the secondary member reference.
- Decoded the club's existing PayNow QR (`image.png`):
  - Payload: `00020101021126520009SG.PAYNOW010120213200913519CSL5030110408299912315204000053037025802SG5925SINGAPORE UNIVERSITY OF T6002SG62290125200913519CSL5EIU6161381696304432B`
  - Proxy: **UEN `200913519CSL5`** (SUTD UEN + sub-account suffix `SL5`)
  - Merchant name: `SINGAPORE UNIVERSITY OF T` (what the VLM checks as recipient)
  - Static QR, **amount editable by payer**, never expires
  - ⚠️ Embeds fixed bill number `200913519CSL5EIU616138169` — possibly used by
    FLYMAX to allocate payments to the club. **Phase 0 must test whether a custom
    bill number still allocates correctly.** (See §13 Phase 0.)
- Membership: **fixed fee per SUTD term**, everyone renews at term start.
- Registration data: full name, SUTD student ID, Telegram username (+ user ID auto).
- Treasurer does NOT want per-payment work. Verification engine = VLM auto-check.
  Exceptions and audits go to the treasurer (accepted, rare/batched).

## 5. Decisions log (agreed with user)

| Decision | Choice |
|---|---|
| Verification engine | Screenshot + VLM auto-check (5 deterministic checks); no human in common path |
| VLM provider | **Gemini Flash** (start on free tier; privacy caveat noted §11; swappable function) |
| Exceptions (failed checks) | DM the treasurer with Approve/Reject buttons |
| Audit loop | **Weekly digest** during collection season; treasurer confirms payer names vs FLYMAX (~5–10 min) |
| Membership model | Fixed fee per SUTD term |
| Reminders | Auto-nag: term-start DM + day 3/7/14 reminders until paid, plus `/remind` manual trigger |
| Admin records | Bot commands + read-only Google Sheet mirror |
| Stack | Python + python-telegram-bot + SQLite, long-polling, free-tier VM |
| Aggregators (Stripe/HitPay/Telegram Payments/Stars) | Ruled out — money must land in the school account; club has no ACRA entity |

## 6. Users & roles

- **Member** — any club member with Telegram. Registers, pays, checks status.
- **Exco admin** — views stats/unpaid lists, triggers reminder rounds.
- **Treasurer** (single role-holder, transferable) — everything admins can do, plus:
  exception queue, audit digests, term setup, manual overrides, admin management.

## 7. Architecture

One Python process on a free-tier VM (Oracle Always Free or GCP e2-micro),
`python-telegram-bot` v21+ with long-polling (no domain/HTTPS needed), SQLite file DB.

Components:
1. **Bot core** — command/conversation handlers for members and admins.
2. **QR generator** — builds per-member dynamic PayNow QR: club UEN proxy, exact
   term fee (non-editable), mandatory school Billing ID in the bill-number field,
   and a unique code (e.g. `BDM-T5-047`) in the reference-label field. Pure
   function: EMVCo TLV payload + CRC-16/CCITT +
   `qrcode` PNG render. School QR's decoded values are config, not hardcoded.
3. **Verification engine** — Gemini Flash (vision) extracts amount, recipient,
   Billing ID, full timestamp, bank reference, and completed-payment status as
   schema-enforced JSON; then plain code checks:
   amount == fee · recipient matches school account name · Billing ID matches ·
   timestamp is within the term and after the member's QR issue time · bank
   reference + image hash have never been submitted in any term. All pass →
   auto-approve. Any fail → exception queue. Model call isolated behind
   `extract_payment_details(image) -> ExtractedPayment` so provider is swappable.
4. **Reminder scheduler** — PTB JobQueue: term-start blast (personal QR attached),
   day 3/7/14 re-nags for unpaid, expiry handling, weekly audit digest.
5. **Audit module** — tracks auto-approved payments since last audit; weekly digest
   to treasurer with ref codes + dates; per-code "missing" button un-marks member,
   notifies them, and flags the incident.
6. **Google Sheet mirror** — one-way sync (bot → Sheet) via gspread + service
   account. Tabs: Members, Payments. Sync on change + nightly full rebuild.
   View-only for exco. Mirror, not database.
7. **SQLite DB** — see §8.

## 8. Data model

- `members(telegram_user_id PK, full_name, sutd_id UNIQUE, username, joined_at, active)`
- `terms(id PK, name, fee_cents, start_date, end_date, created_by, created_at)`
- `payments(id PK, member_id FK, term_id FK, ref_code UNIQUE, status, amount_cents,
   screenshot_file_id, extracted_json, bank_txn_id UNIQUE NULLABLE, image_hash,
   qr_issued_at, payment_timestamp, created_at, verified_at, verified_by)`
- `receipt_fingerprints(payment_id FK, image_hash UNIQUE, bank_txn_id UNIQUE,
   submitted_at)` — permanent cross-term anti-reuse history
  - `status ∈ {awaiting_payment, pending_verification, verified, exception, rejected, revoked}`
  - `verified_by ∈ {auto, treasurer, manual_override}`
- `admins(telegram_user_id PK, role ∈ {treasurer, admin}, added_by, added_at)`
- `audits(id PK, period_start, period_end, payment_count, result, audited_at)`
- `settings(key PK, value)` — PayNow proxy, merchant name, bill-number strategy, etc.

Members keyed by Telegram **user ID** (never @username — usernames change).

## 9. Flows

**Registration:** `/start` → name → SUTD ID → confirm → registered. Re-running
`/start` shows status instead.

**Payment:** member taps Pay (or term-start DM) → bot sends personal QR (exact fee
+ ref code) + instructions → member pays in bank app → uploads screenshot → engine
verifies → ✅ receipt + membership active until term end, Sheet updated
— or → exception → treasurer ping → Approve/Reject → member notified.

**Audit (weekly during collection season):** digest of auto-approved payments →
treasurer checks FLYMAX by payer name, amount, and date → "✓ all found" or marks missing payments → bot revokes +
notifies + flags those members. Quiet weeks (0 payments) send nothing.

**Term rollover:** treasurer `/newterm Name fee start end` → at start date, blast
renewal DMs with fresh QRs; statuses reset to awaiting_payment; expiry at end date.

**Exception handling details:** unreadable screenshot → bot asks member to retake
(never bothers treasurer with blur). Gemini API down → payment queues as
pending_verification, retried hourly, member told "being processed".

## 10. Commands

Member: `/start` (register), `/pay` (get QR), `/status`, `/help`
Admin: `/stats`, `/unpaid`, `/members`, `/remind`
Treasurer: `/newterm`, `/markpaid <sutd_id>` (manual override, logged),
`/audit` (digest on demand), `/addadmin`, `/removeadmin`, `/transfertreasurer`,
`/relink <sutd_id>` (member changed Telegram account), `/settings`

## 11. Security & privacy

- Secrets (bot token, Gemini key, service-account JSON) in `.env` / env vars on VM.
- Screenshots NOT stored on VM — keep Telegram `file_id` + extracted fields + hash.
- Data minimal: name, SUTD ID, Telegram ID, payment records. Admin-only access.
- Gemini free tier may use content for product improvement → decision before
  launch: stay free vs paid key (~$0.20–0.60/term). Code identical either way.
- Audit trail: every verification/override records who/what/when.
- VLM reads, code decides: model output never directly grants membership; the five
  deterministic checks do.

## 12. Edge cases

| Case | Handling |
|---|---|
| Wrong amount (bank app allowed edit) | amount check fails → exception |
| Exact copied screenshot | permanent SHA-256 image fingerprint rejects it |
| Cropped/recompressed copied screenshot | permanent normalized bank reference rejects it |
| Previous-term receipt not seen before | payment timestamp predates current QR issue and fails |
| Blurry screenshot | bot asks to retake (no exception raised) |
| Confirm-screen (not success) screenshot | `is_success_screen` false → ask member for the success screen |
| Member switches Telegram account | `/relink` by SUTD ID |
| Cash / no-Telegram member | `/markpaid` manual override, logged |
| Bot/VM restart | SQLite on disk; long-polling resumes; jobs rebuilt from DB at startup |
| School changes QR/account | proxy + merchant name live in `settings` |
| Audit finds missing payment | revoke + notify + flag; repeated flags surfaced to exco |

## 13. Build phases

**Phase 0 — validation spike (COMPLETED 2026-06-20):**
1. ✅ Decode school QR (done — see §4).
2. ✅ Generate test QR **variant A**: school's original bill number kept, our code in
   a different EMVCo subfield; **variant B**: our ref code as the bill number.
3. ✅ Pay S$0.10 with each variant from a personal bank app.
4. ✅ Treasurer checks FLYMAX: (a) did both payments arrive in the club account?
   (b) which field(s) show in history? (c) does variant B break allocation?
5. ✅ Outcome: variant A arrived; variant B did not clear. FLYMAX displayed the
   payer name and original Billing ID `200913519CSL5EIU616138169`, but not the
   variant A reference-label code `BDMTEST01`. Therefore every real QR must keep
   the original school bill number. Bank-side audits fall back to payer-name
   matching, supported by amount/date; audit digests list expected payer names.

**Phase 1 — core bot:** registration, SQLite, `/status`, admin bootstrap.
**Phase 2 — payment engine:** QR generation, screenshot intake, Gemini extraction
+ 5 checks, receipts, exception queue.
**Phase 3 — lifecycle:** terms, reminders (term-start, 3/7/14), audit digest.
**Phase 4 — visibility:** Google Sheet mirror, full admin command set.
**Phase 5 — ship:** deploy to free VM (systemd service), handover README, test
with 2–3 real members before term-wide launch.

## 14. Testing

- Unit (pytest): EMVCo payload builder vs known-good QR vectors (incl. re-encoding
  the school's own QR byte-identically), CRC, the 5 checks, ref-code uniqueness,
  state machine transitions, reminder scheduling logic.
- VLM prompt: golden-set of real SG banking screenshots (DBS, PayLah!, OCBC, UOB)
  → assert extracted JSON; run on demand, not in CI (costs/keys).
- Integration: PTB test harness with fake updates for each flow.
- E2E: test bot token + real Gemini key, manual script before each phase ships.

## 15. Costs

Hosting $0 (free-tier VM) · Bot $0 · Gemini $0 (free tier) or ≤$1/term paid ·
Phase 0 test payments ~$0.30 (recoverable — they land in the club account).

## 16. Open questions (non-blocking)

1. ✅ Phase 0 outcome: preserve the original school bill number; use payer-name
   matching for FLYMAX audits because its history does not show the reference label.
2. Email to SUTD finance: can the account send per-transaction email alerts?
   If ever yes → verification upgrades to fully bank-confirmed; design unchanged.
3. Term fee amount + current term dates (needed at Phase 3 config, not before).
4. Gemini free vs paid tier — decide at launch (privacy, §11).
