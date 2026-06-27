# SUTD Badminton Club Bot

Telegram bot that registers club members, collects term fees via PayNow QR,
and verifies payment screenshots automatically. Design doc:
`docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`. Living status:
`MEMORY.md`.

**Going live:** see **[`DEPLOY.md`](DEPLOY.md)** for the step-by-step free-VM
(systemd) deployment and pre-launch checklist.

## Commands

**Members:** `/start` (register / status), `/pay` (get your QR), `/status`,
`/help`, `/cancel`. After paying, send the payment screenshot to the bot.

**Admins:** `/unpaid`, `/stats`, `/members`, `/settings` (view PayNow config).

**Treasurer (everything admins can do, plus):** `/newterm <name> <fee> <start>
<end>`, `/markpaid <sutd_id>`, `/remind`, `/audit`, `/flag <sutd_id>`,
`/revoke <sutd_id>`, `/addadmin <sutd_id>`, `/removeadmin <sutd_id>`,
`/transfertreasurer <sutd_id>`, `/relink <sutd_id> [new_id]`,
`/settings set <key> <value>`, `/sync` (refresh the Google Sheet),
`/backup` (save a database backup).

## Setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
# edit .env: bot token, your Telegram ID, and Gemini API key
```

## Run the bot

```powershell
.venv\Scripts\python -m clubbot
```

Stop with Ctrl+C. The database is a single file (`clubbot.db`).

## Test the payment flow

As the treasurer, create an active S$0.05 test term:

```text
/newterm Payment Test 0.05 2026-06-20 2026-07-20
```

A registered member can then send `/pay`, pay with the generated QR, and send
the completed-payment screenshot back to the bot. The fee is stored per term;
after testing, create the real term with `20.00` instead of changing code.

The bot rejects reused receipts using two permanent identifiers:

- SHA-256 fingerprint of every submitted image.
- Normalized bank transaction/reference number extracted from the receipt.

It also records when `/pay` first issued the member's QR and rejects receipts
dated before that time. These records must be preserved with `clubbot.db`.

## Run the tests

```powershell
.venv\Scripts\python -m pytest
```

## Phase 0: QR placement test (completed)

```powershell
.venv\Scripts\python scripts/make_phase0_qrs.py
```

The test established that every generated QR must preserve the school's Billing
ID. The tested bank receipt and DBS FLYMAX do not expose a shared member
reference, so verification uses Billing ID, amount, timestamp, bank reference,
and permanent duplicate protection.
