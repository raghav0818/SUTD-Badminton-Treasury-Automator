# SUTD Badminton Club Payment Bot

**START HERE every session:**
1. Read `MEMORY.md` — living project status, decisions, and next steps.
2. Read the PRD at `docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`
   before touching code.
3. **Before ending any work session, update `MEMORY.md`** (status, decisions made,
   next steps). This is mandatory — it is how sessions hand off to each other.

## Ground rules

- The user is the club treasurer, not a professional developer — explain choices
  plainly, ask before scope changes.
- Never store payment screenshots on disk; keep Telegram file_id + extracted
  fields only. Secrets go in `.env` (gitignored), never in code.
- The VLM (Gemini Flash) only *extracts* data from screenshots; membership
  decisions are made by deterministic checks in code (PRD §7.3).
- Members are keyed by Telegram user ID, never by @username.
- The school account is called **DBS FLYMAX**.
- Phase 0 is complete: preserve the original school Billing ID in every QR;
  check `MEMORY.md` for the full outcome before building Phase 2.
- Use **S$0.05** as the initial Phase 2 end-to-end test term fee. Keep fees
  configurable per term; do not hardcode S$0.05 or the later S$20.00 fee.
- Phase 2 is implemented: `/newterm`, `/pay`, receipt extraction, deterministic
  verification, duplicate protection, and treasurer Approve/Reject review.
- Tested payer receipts do not display the QR's `BDM...` reference. Do not
  require it. Verify Billing ID, amount, recipient, full timestamp after QR
  issue, and globally unique image hash + normalized bank reference instead.
- `receipt_fingerprints` is permanent anti-reuse history. Never clear it between
  terms, and back up `clubbot.db` (and the `backups/` directory it writes).
- Phases 3 and 4 are implemented: lifecycle/reminders/audit; admin management
  (`/addadmin` `/removeadmin` `/transfertreasurer`), account `/relink`, editable
  PayNow `/settings`, the read-only Google Sheet mirror, plus a robustness layer
  (global error handler, per-user receipt rate limiting, WAL, daily backups).
- Routing-critical settings (`paynow_uen`, `bill_number`) require an in-bot
  confirmation before they change — a wrong value silently breaks DBS FLYMAX
  routing (Phase 0). The QR build and receipt verification read the effective
  PayNow config, so they always agree.
- Deploy/run guidance lives in `DEPLOY.md`. The Google Sheet mirror is optional
  and isolated: a Sheets failure is logged and never blocks a member's flow.

## Stack

Python 3.12+ · python-telegram-bot v21+ (long-polling) · SQLite · google-genai
(Gemini Flash) · gspread · qrcode · pytest. Target: free-tier Linux VM, systemd.
