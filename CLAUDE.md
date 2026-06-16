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
- Phase 0 (QR reference-placement test) is BLOCKING for the payment engine —
  check MEMORY.md for its outcome before building Phase 2.

## Stack

Python 3.12+ · python-telegram-bot v21+ (long-polling) · SQLite · google-genai
(Gemini Flash) · gspread · qrcode · pytest. Target: free-tier Linux VM, systemd.
