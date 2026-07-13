# SUTD Badminton Club Bot

Telegram bot that collects club membership fees. Members register, get a
personal PayNow QR, pay, and send back the payment screenshot — the bot
verifies it automatically. You (the treasurer) only approve rare exceptions
and confirm a weekly audit list against DBS FLYMAX.

Bot: **SUTD ShuttleBuddy** (handle: `@MyClubFinanceBot`) · Runs 24/7 on a
Raspberry Pi 4.

---

## Telegram commands

### Everyone (members)

| Command | What it does |
|---|---|
| `/start` | Register (name → SUTD ID → confirm). If already registered, shows status. |
| `/status` | Membership + payment status for the current term. |
| `/pay` | Get your personal PayNow QR for the current term. |
| `/help` | List commands. |
| *(send a photo)* | Submit your payment screenshot for verification. |

### Admins (exco)

| Command | What it does |
|---|---|
| `/unpaid` | Who hasn't paid this term. |
| `/stats` | Term summary: registered / paid / unpaid / exceptions / flagged. |
| `/members` | All registered members. |

### Treasurer only

| Command | What it does |
|---|---|
| `/newterm <name> <fee> <start> <end>` | Open fee collection, e.g. `/newterm Term 1 20.00 2026-09-01 2026-12-01`. Members get their QR automatically at 10:00 on the start date, and unpaid members one reminder on day 7. |
| `/markpaid <sutd_id>` | Record a cash/manual payment. |
| `/remind` | Nudge all unpaid members right now. |
| `/audit` | Get the FLYMAX check-list now (also arrives automatically Monday 09:00). Tap "All found" after checking the bank app. |
| `/flag <sutd_id>` | Mark a payment you couldn't find in FLYMAX (no member impact). |
| `/revoke <sutd_id>` | Remove a verified membership (member is notified). |
| `/addadmin <sutd_id>` / `/removeadmin <sutd_id>` | Manage exco admins. |
| `/transfertreasurer <sutd_id>` | Hand over the treasurer role (you stay admin). |
| `/relink <sutd_id>` | Member changed Telegram account: arm this, they re-register with `/start` from the new account within 48 h and their history moves over. `/relink` alone lists armed relinks; `/relink <sutd_id> cancel` disarms. |
| `/settings` | View/change the PayNow values (UEN, merchant name, Billing ID, recipient match). Only needed if the school ever changes its account. |

When a receipt fails a check you get a DM with **Approve / Reject** buttons —
that is the whole exception workflow.

---

## Run it on your PC (for testing)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env        # then fill in .env (see below)
.venv\Scripts\python scripts\preflight.py   # all lines must say PASS
.venv\Scripts\python -m clubbot             # Ctrl+C to stop
```

Run the tests: `.venv\Scripts\python -m pytest`

### .env values

| Key | Where it comes from |
|---|---|
| `BOT_TOKEN` | @BotFather in Telegram |
| `TREASURER_TELEGRAM_ID` | your numeric ID (@userinfobot) |
| `DB_PATH` | leave as `clubbot.db` |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | leave as `gemini-2.5-flash` |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | path to the service-account JSON key (optional, for the Sheet) |
| `SHEET_ID` | long ID in the Google Sheet's URL (optional) |

Google Sheet mirror (optional): create a Sheet, put its ID in `SHEET_ID`,
and **share the Sheet (Editor)** with the service account's `client_email`
from the JSON file. The bot rebuilds Members/Payments tabs ~30 s after any
change plus nightly at 02:30. The Sheet is read-only output — editing it
changes nothing.

---

## Deploy / update on the Raspberry Pi 4 (24/7)

The bot lives at `~/clubbot` on the Pi. The SAME two steps do both the first
install and every later update — `setup_pi.sh` is safe to re-run.

**Step 1 — copy the code + secrets from the PC** (run in THIS project folder;
replace `<user>@<pi>` with your Pi login, e.g. `blud@blud.local`):

```powershell
ssh <user>@<pi> "mkdir -p clubbot"
scp -r clubbot scripts deploy requirements.txt .env service-account.json <user>@<pi>:clubbot/
```

**Step 2 — install/restart on the Pi** (in an ssh window):

```bash
bash ~/clubbot/deploy/setup_pi.sh
~/clubbot/.venv/bin/python ~/clubbot/scripts/preflight.py   # all lines PASS
```

The script ends by printing the service status — look for `active (running)`.
The service restarts itself after crashes and reboots. Long-polling means no
port forwarding — home Wi-Fi is fine.

### Daily database backup (do this — the DB is irreplaceable)

On the Pi (replace `<user>` with your Pi username):

```bash
mkdir -p ~/clubbot/backups
echo '0 3 * * * <user> cp /home/<user>/clubbot/clubbot.db /home/<user>/clubbot/backups/clubbot-$(date +\%F).db' | sudo tee /etc/cron.d/clubbot-backup
```

### Operating it

```bash
journalctl -u clubbot -f              # watch live logs
sudo systemctl restart clubbot        # restart
sudo systemctl stop clubbot           # stop
```

To update the code later: redo Step 1 + Step 2 above. Never run the bot on
the PC while the Pi service is running — two copies fight over Telegram.

### Before the first real term

Delete the test data ONCE, before any real member pays:

```bash
sudo systemctl stop clubbot
rm ~/clubbot/clubbot.db
sudo systemctl start clubbot
```

Never delete `clubbot.db` again after that — it holds the permanent
receipt-reuse protection.

---

## Handover to the next treasurer

1. `/transfertreasurer <their sutd_id>` in Telegram.
2. Give them the GitHub repo, the Pi login, and the secrets (`.env`,
   `service-account.json`).
3. Point them at `CLAUDE.md` (project status/decisions) and the design doc in
   `docs/superpowers/specs/`.
