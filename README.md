# SUTD Badminton Club Bot

Telegram bot that registers club members, collects term fees via PayNow QR,
and verifies payment screenshots automatically. Design doc:
`docs/superpowers/specs/2026-06-11-club-payment-bot-design.md`. Living status:
`MEMORY.md`.

## Setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
# edit .env: bot token from @BotFather, your Telegram ID from @userinfobot
```

## Run the bot

```powershell
.venv\Scripts\python -m clubbot
```

Stop with Ctrl+C. The database is a single file (`clubbot.db`).

## Run the tests

```powershell
.venv\Scripts\python -m pytest
```

## Phase 0: QR placement test (do this once)

```powershell
.venv\Scripts\python scripts/make_phase0_qrs.py
```

Pay S$0.10 with each generated QR (`phase0_qrs/`) from your personal bank
app, then check the DBS Flimax history and report what each payment shows.
The outcome unblocks the payment engine (Phase 2).
