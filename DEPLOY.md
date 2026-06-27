# Going Live — SUTD Badminton Club Bot

This guide takes the bot from a working build to a 24/7 service on a **Linux
machine** — either a free cloud VM, or a **Raspberry Pi / old laptop you own** (no
credit card needed). It is written for the treasurer, not a professional
sysadmin — follow it top to bottom. Lines starting with `$` are commands you type
in the machine's terminal.

The bot uses **long-polling**, so it needs no domain, no HTTPS, and no open ports.
It only needs to stay running and reach the internet.

> **No credit card?** Cloud providers (Oracle/AWS/GCP) ask for a card only to
> verify identity — Always Free never charges it, and a **debit card** or a
> **Revolut/YouTrip/Wise virtual card** usually works. If you have none, use the
> **self-host** option in Section 1 — a Raspberry Pi or an old laptop runs this
> bot fine and costs nothing to keep online.

---

## What you need before you start

1. A **Telegram bot token** — from [@BotFather](https://t.me/BotFather): send
   `/newbot`, follow the prompts, copy the token (looks like `123456:ABC-...`).
2. **Your numeric Telegram user ID** — message [@userinfobot](https://t.me/userinfobot);
   it replies with your `Id`. You are the treasurer.
3. A **Gemini API key with billing enabled** (you chose the paid tier for privacy)
   — from [Google AI Studio](https://aistudio.google.com/apikey), then enable
   billing on its Google Cloud project. The code is identical for the free tier;
   billing just stops Google training on submitted receipts.
4. **(Optional) Google Sheet mirror** — a Google service-account JSON key and a
   Google Sheet. See [Section 6](#6-google-sheet-mirror-optional). Skip the Sheet
   vars in `.env` to run without it.

---

## 1. Pick where it runs

Choose **one**. Either way you end up SSH'd into a Linux machine running **Ubuntu
24.04** (which ships Python 3.12, exactly what the bot needs), and Sections 2–12
are then identical.

### Option A — free cloud VM (needs a card for verification)

**Oracle Cloud Always Free** (genuinely free forever):

1. Sign up at <https://www.oracle.com/cloud/free/> (a debit or virtual card works).
2. Create a **Compute instance** → image **Canonical Ubuntu 24.04**, shape
   **VM.Standard.A1.Flex** (Ampere/ARM) or **E2.1.Micro**.
3. Download the SSH private key; note the instance's **public IP**.
4. SSH in: `ssh -i /path/to/your-key ubuntu@YOUR_PUBLIC_IP`

(A **GCP e2-micro** free-tier VM works the same way.)

### Option B — self-host on a Raspberry Pi or old laptop (no card)

This is the best no-card option: free (or ~S$60 once for a Pi), and truly 24/7.

**Install Ubuntu 24.04 on the device:**

- **Raspberry Pi** (a Pi 4 or 5 is ideal; a Pi Zero 2 W also works): install the
  [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your normal PC,
  insert the SD card, and choose **Ubuntu Server 24.04 LTS (64-bit)** as the OS.
  Click the gear/⚙ (Edit Settings) and set: a **hostname** (e.g. `clubbot`),
  **enable SSH**, a **username + password**, and your **Wi-Fi** name/password.
  Write the card, put it in the Pi, and power it on.
- **Old laptop:** download the **Ubuntu Server 24.04 LTS** ISO, write it to a USB
  stick with [balenaEtcher](https://etcher.balena.io/), boot the laptop from the
  USB, and install (this erases the laptop — back up anything you want first).
  During install, tick **"Install OpenSSH server"**.

**Find the device's IP and SSH in from your normal PC:**

- On the device's own screen you can run `hostname -I` to see its IP, or check
  your home router's device list. Then from your PC:
  `ssh your-username@DEVICE_IP` (use the username you set during install).

**Keep it awake and stable (do this once it's set up):**

- **Old laptop:** stop it sleeping when you close the lid —
  `sudo sed -i 's/#HandleLidSwitch=suspend/HandleLidSwitch=ignore/' /etc/systemd/logind.conf && sudo systemctl restart systemd-logind`
- Prefer a **wired Ethernet** connection if you can; leave the device **plugged
  into power**. Off-device backups (Section 11) matter even more here, since SD
  cards can wear out — copying backups to your PC protects the payment records.

## 2. Install system dependencies

Ubuntu 24.04 ships Python 3.12, which is what the bot needs:

```
$ sudo apt update && sudo apt -y upgrade
$ sudo apt -y install python3.12 python3.12-venv git
$ python3.12 --version      # expect Python 3.12.x
```

## 3. Get the code

```
$ git clone <your-repo-url> clubbot
$ cd clubbot
```

(If the repo is private, set up a deploy key or use `gh`/HTTPS with a token.)

## 4. Create the virtual environment and install requirements

```
$ python3.12 -m venv .venv
$ .venv/bin/python -m pip install --upgrade pip
$ .venv/bin/python -m pip install -r requirements.txt
```

## 5. Telegram token, treasurer ID, Gemini key

You collected these in "What you need". You will put them in `.env` in Section 7.

## 6. Google Sheet mirror (optional)

The bot can mirror Members and Payments into a read-only Google Sheet for the exco.
To enable it:

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or
   reuse) a project and **enable the Google Sheets API** and **Google Drive API**.
2. **IAM & Admin → Service Accounts → Create service account.** Give it a name,
   create a **JSON key**, and download it. Copy that file onto the VM, e.g.
   `~/clubbot/service-account.json` (it is gitignored — never commit it).
3. Create a Google Sheet. Copy its **ID** from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_ID`**`/edit`.
4. **Share** the Sheet with the service account's email (looks like
   `name@project.iam.gserviceaccount.com`) as **Editor**.

The bot creates the `Members` and `Payments` tabs automatically on first sync.

## 7. Create the `.env` file

```
$ cp .env.example .env
$ nano .env
```

Fill it in:

```
BOT_TOKEN=123456:your-real-bot-token
TREASURER_TELEGRAM_ID=your-numeric-id
DB_PATH=clubbot.db
GEMINI_API_KEY=your-billing-enabled-gemini-key
GEMINI_MODEL=gemini-2.5-flash
# Optional Google Sheet mirror (leave blank to disable):
GOOGLE_SERVICE_ACCOUNT_JSON=/home/ubuntu/clubbot/service-account.json
SHEET_ID=your-sheet-id
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`). The file is gitignored.

## 8. First run (smoke test)

```
$ .venv/bin/python -m clubbot
```

You should see log lines and no errors. In Telegram, message your bot `/start`,
then `/help` — as the treasurer you should see the admin command list. Press
`Ctrl+C` to stop. If it complains about a missing token/ID, re-check `.env`.

## 9. Run it 24/7 with systemd

Create the service unit:

```
$ sudo nano /etc/systemd/system/clubbot.service
```

Paste (adjust `User` and paths if your username/dir differ):

```ini
[Unit]
Description=SUTD Badminton Club Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/clubbot
ExecStart=/home/ubuntu/clubbot/.venv/bin/python -m clubbot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```
$ sudo systemctl daemon-reload
$ sudo systemctl enable --now clubbot
$ sudo systemctl status clubbot      # should say "active (running)"
```

`Restart=on-failure` means the bot restarts automatically if it ever crashes.
On reboot it comes back on its own. Its scheduled jobs (term-start blast, day-7
reminder, weekly audit, nightly backup, nightly Sheet rebuild) are rebuilt from
the database at startup, so a restart never double-sends or loses them.

## 10. Logs

```
$ journalctl -u clubbot -f          # live tail
$ journalctl -u clubbot --since "1 hour ago"
```

If the bot hits an unexpected error it logs the full traceback here **and** DMs
you a short alert (rate-limited so it can't spam you).

## 11. Backups — protect `clubbot.db`

The database holds your members, payment records, and the **permanent
`receipt_fingerprints`** anti-reuse history. The bot writes a daily backup to
`backups/clubbot-YYYYMMDD-HHMMSS.db` (03:00 SGT) and keeps the most recent 14,
and you can take one anytime with the `/backup` command.

**Also copy backups off the device** so a lost VM or a dead SD card does not lose
your data. From your own computer, periodically:

```
# Raspberry Pi / old laptop (use the username + IP you set up):
$ scp "your-username@DEVICE_IP:clubbot/backups/*" ./clubbot-backups/
# Oracle/cloud VM (add the key file):
$ scp -i /path/to/your-key "ubuntu@YOUR_PUBLIC_IP:clubbot/backups/*" ./clubbot-backups/
```

**To restore:** stop the bot, replace the DB, restart:

```
$ sudo systemctl stop clubbot
$ cp backups/clubbot-YYYYMMDD-HHMMSS.db clubbot.db
$ sudo systemctl start clubbot
```

## 12. Pre-launch checklist (do this once, before announcing to the club)

The repo's `clubbot.db` contains **test data** (e.g. the test member "noobslayer").
Start the real launch from a clean database:

1. Stop the bot: `sudo systemctl stop clubbot`
2. Remove the test database and its WAL side-files:
   `rm -f clubbot.db clubbot.db-wal clubbot.db-shm`
3. Start the bot: `sudo systemctl start clubbot` (a fresh DB is created and you are
   re-bootstrapped as treasurer from `.env`).
4. Have **2–3 real members** `/start` and register.
5. Open the real term:
   `/newterm Term Name 20.00 2026-07-01 2026-09-30`
   (use your real fee and dates; the fee is per-term, never hardcoded).
6. Have one member run `/pay`, pay the real **S$20**, send the screenshot, and
   confirm the bot replies "Verified". Cross-check it appears in **DBS FLYMAX**.
7. When that round-trip works, announce the bot to the whole club.

---

## Everyday maintenance

- **Update the code:**
  ```
  $ cd ~/clubbot && git pull
  $ .venv/bin/python -m pip install -r requirements.txt
  $ sudo systemctl restart clubbot
  ```
- **Change the term fee / dates:** just `/newterm` a new term — never edit code.
- **Change the school PayNow account** (rare): `/settings` (the UEN and Billing ID
  ask for confirmation, because the wrong value stops money reaching DBS FLYMAX).

## Troubleshooting

| Symptom | Check |
|---|---|
| Bot doesn't respond | `sudo systemctl status clubbot`; `journalctl -u clubbot -e` |
| "Receipt verification is not configured" | `GEMINI_API_KEY` is set in `.env`, then restart |
| Sheet not updating | service-account JSON path + `SHEET_ID` correct, Sheet shared as Editor; run `/sync` |
| Crash loop after an update | `journalctl -u clubbot -e` for the traceback; `git pull` may need `pip install -r requirements.txt` |
| Lost the VM / dead SD card | restore the latest off-device backup onto a fresh machine (Sections 1–9 + 11) |
