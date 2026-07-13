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

## Google Sheet mirror (optional)

Gives the exco a read-only view of Members and Payments. To enable:

1. In Google Cloud Console, create a project, enable the **Google Sheets API**,
   and create a **service account**. Download its JSON key file.
2. Create a Google Sheet and share it (Viewer is not enough — use Editor) with
   the service account's email address (it looks like
   `something@project.iam.gserviceaccount.com`).
3. In `.env`, set `GOOGLE_SERVICE_ACCOUNT_FILE` to the JSON file path and
   `SHEET_ID` to the long ID in the Sheet's URL.

The bot rebuilds the Members and Payments tabs about 30 seconds after any
change, plus a full nightly rebuild at 02:30 SGT. The Sheet is a mirror only;
editing it changes nothing in the bot.

## Deploy to a Linux VM (systemd)

```bash
sudo useradd -r -m -d /opt/clubbot clubbot
sudo -u clubbot git clone <this-repo> /opt/clubbot
cd /opt/clubbot
sudo -u clubbot python3 -m venv .venv
sudo -u clubbot .venv/bin/pip install -r requirements.txt
sudo -u clubbot cp .env.example .env   # then edit .env with real values
sudo cp deploy/clubbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clubbot
journalctl -u clubbot -f               # watch the logs
```

Back up the database daily — `clubbot.db` holds all membership, payment, and
permanent anti-reuse history and must never be lost between terms:

```bash
sudo -u clubbot mkdir -p /opt/clubbot/backups
echo '0 3 * * * clubbot cp /opt/clubbot/clubbot.db /opt/clubbot/backups/clubbot-$(date +\%F).db' | sudo tee /etc/cron.d/clubbot-backup
```

## Handover to the next treasurer

1. Run `/transfertreasurer <their SUTD ID>` in Telegram (they must be a
   registered member). You stay on as an admin; they get everything else.
2. Give them access to this repository, the VM, and the `.env` secrets
   (bot token, Gemini key, service-account JSON).
3. Point them at `MEMORY.md` (project status) and
   `docs/superpowers/specs/2026-06-11-club-payment-bot-design.md` (full design).
4. If a member changes Telegram account: `/relink <sutd_id>`, then they
   re-register with `/start` from the new account. History moves over.

## Phase 0: QR placement test (completed)

```powershell
.venv\Scripts\python scripts/make_phase0_qrs.py
```

The test established that every generated QR must preserve the school's Billing
ID. The tested bank receipt and DBS FLYMAX do not expose a shared member
reference, so verification uses Billing ID, amount, timestamp, bank reference,
and permanent duplicate protection.
