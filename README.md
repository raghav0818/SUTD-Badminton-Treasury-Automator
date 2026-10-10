# SUTD Badminton Club Bot

Telegram bot that collects club membership fees. Members register, get a
personal PayNow QR, pay, and send back the payment screenshot — the bot
verifies it automatically. You (the treasurer) only approve rare exceptions
and, once a term, check the paid totals against DBS FLYMAX.

Bot: **SUTD ShuttleBuddy** (handle: `@MyClubFinanceBot`) · Runs 24/7 on a
Raspberry Pi 4.

---

## Telegram commands

Tap `/` (or the Menu button) in the chat with the bot to see only the
commands you can use; admins and the treasurer see theirs too.

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
| `/unpaid` | Who hasn't paid this term, plus roster people who never opened the bot, and how many opted out. The treasurer also gets a **Mark paid** button per person (lists of up to 30) for cash payments, each asking to confirm. |
| `/setgroup rec` / `/setgroup comp` | Send **inside** the rec or competitive group chat (make the bot a group **admin** first, so Telegram lets it check who is in the group at registration) to link it. The bot then posts paid counts (no names) plus a tap-to-pay link there on Mondays and Thursdays at 12:00 until the deadline. **Rec chat only:** the bot also needs the admin rights **Ban users** and **Invite users via link**; it then revokes the old main invite link and replies with a new **join link** (joining asks the bot, which welcomes the person by DM and lets them in, except people removed for not paying, until they pay). Revoke any other old links by hand. |
| `/stats` | Term summary: count × price per category (compare once per term against FLYMAX), total, roster paid, registered / paid / unpaid / opted out / exceptions. |
| `/roster` | Replace the competitive roster: `/roster` then one `Name, 1010xxx` line per person in the same message. If any line is invalid nothing changes, so a typo can never drop a player. `/roster` alone shows the current size. |
| `/members` | All registered members. |

### Treasurer only

| Command | What it does |
|---|---|
| `/newterm` | Open fee collection. Send it **on its own** and tap through 5 steps: name (suggests the next one, e.g. Term 2), start date (Today / Next Monday), end date (same length as last term / 13 weeks), deadline (2 or 3 weeks after start), prices (**All same as last term**, or one by one). Any step also accepts a typed answer (`2026-09-01`, `1/9/2026` or `1 Sep 2026`); `/cancel` stops; nothing is created until **Create term**. The one-line form still works, e.g. `/newterm Term 1 2026-09-01 2026-12-01 deadline=2026-09-15 comp=20 rec=25 recshirt=30 shirt=15` (competitive S$20, or S$35 with the S$15 shirt; rec S$25, or S$30 with shirt). Members get a "Pay now" message at 10:00 on the start date; unpaid members get reminders at 10:00 on days 3, 7, 10 and 13 (only those before the deadline) plus a last call on the deadline day. Each has a "Not continuing this term" button that stops their reminders (they can still `/pay` later). Tapping Pay now asks shirt yes/no (and size), then sends the QR at that amount. No shirt on sale this term? Use `shirt=0` and set `recshirt` equal to `rec`, and the shirt question is skipped. |
| `/markpaid <sutd_id>` | Record a cash/manual payment (shows the name and asks you to confirm). |
| `/remind` | Nudge all unpaid members right now. |
| `/revoke <sutd_id>` | Remove a verified membership (asks you to confirm; member is notified). |
| `/addadmin <sutd_id>` / `/removeadmin` | Manage exco admins. `/removeadmin` alone shows a button per admin. |
| `/transfertreasurer <sutd_id>` | Hand over the treasurer role (asks you to confirm; you stay admin). |
| `/relink <sutd_id>` | Member changed Telegram account: arm this, they re-register with `/start` from the new account within 48 h and their history moves over. `/relink` alone lists armed relinks with a Cancel button each. |
| `/settings` | View/change the PayNow values (UEN, merchant name, Billing ID, recipient match). Only needed if the school ever changes its account. |
| `/settings group_posts off` / `on` | Stop or resume the twice-weekly group progress posts (default on). |
| `/settings remove_unpaid off` / `preview` / `on` | Removing unpaid people from the **rec chat only** (the comp chat is never touched). Default `preview`: 2 days before removal day you get the list, but nobody is removed. `on`: unpaid people also get a warning DM then, and on removal day (deadline + grace days, 10:00) everyone still unpaid is removed; late joiners are removed when their own grace runs out. A pending/under-review receipt, any verified payment (rec or comp), admins and the Keep list are never removed. Removed people can rejoin with the join link once they've paid. |
| `/settings removal_grace_days <n>` | Days after the deadline before unpaid people are removed (default 7). |
| `/keep` | People never removed from the rec chat (coaches, alumni). Add them with the **Keep** buttons in the removal preview; `/keep` shows an Unkeep button each. |

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
| `GEMINI_MODEL` | leave as `gemini-3.1-flash-lite` |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | path to the service-account JSON key (optional, for the Sheet) |
| `SHEET_ID` | long ID in the Google Sheet's URL (optional) |

Google Sheet mirror (optional): create a Sheet, put its ID in `SHEET_ID`,
and **share the Sheet (Editor)** with the service account's `client_email`
from the JSON file. The bot rebuilds Members/Payments tabs ~30 s after any
change plus nightly at 02:30. The Sheet is read-only output — editing it
changes nothing.

---

## Deploy / update on the Raspberry Pi 4 (24/7)

The bot lives at `~/clubbot` on the Pi, as a `git clone` of this (public)
GitHub repo. Run every command below on the Pi, in an ssh window
(e.g. `ssh blud@blud.local`).

### Updating (the normal case)

```bash
bash ~/clubbot/deploy/update.sh
```

It downloads the latest code from GitHub, installs anything new, and restarts
the bot. Your `.env`, `service-account.json` and `clubbot.db` are not in git,
so updates never touch them. The script ends by printing the service status —
look for `active (running)`.

### First-time setup (or switching an old scp-copied `~/clubbot` to git)

Run these one at a time. They put the old folder aside, download a fresh copy,
and bring your secrets and database back:

```bash
sudo systemctl stop clubbot                 # "not loaded" on a brand-new Pi is fine
sudo apt install -y git
mv ~/clubbot ~/clubbot-old                  # skip on a brand-new Pi
git clone https://github.com/raghav0818/SUTD-Badminton-Treasury-Automator.git ~/clubbot
sudo cp ~/clubbot-old/.env ~/clubbot-old/service-account.json ~/clubbot/
sudo cp ~/clubbot-old/clubbot.db ~/clubbot/   # "No such file" is fine: no data yet
sudo chown -R "$USER:$USER" ~/clubbot
bash ~/clubbot/deploy/setup_pi.sh
~/clubbot/.venv/bin/python ~/clubbot/scripts/preflight.py   # all lines PASS
```

On a brand-new Pi there is no `~/clubbot-old`: instead of the two `sudo cp`
lines, copy the secrets over from the PC (run in THIS project folder on the
PC): `scp .env service-account.json blud@blud.local:clubbot/`

Once the bot runs fine for a week, delete the old copy: `rm -rf ~/clubbot-old`
(use `sudo rm -rf` if it says Permission denied).

The service restarts itself after crashes and reboots. Long-polling means no
port forwarding — home Wi-Fi is fine.

### Database backups (the DB is irreplaceable)

Once a week (checked daily at 03:00 and at startup, so downtime delays it instead of skipping it) the bot DMs the treasurer a copy of `clubbot.db` on
Telegram. Keep those files (don't delete the chat). For an extra daily copy on
the Pi itself (replace `<user>` with your Pi username):

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

Never run the bot on the PC while the Pi service is running — two copies
fight over Telegram.

### One-time: record people already in the rec chat

A Telegram bot cannot list a group's members, so people who were in the rec
chat before the bot was watching (and never registered or rejoined) are
invisible to it. Import them once, after `/setgroup rec`: get an `api_id` and
`api_hash` at https://my.telegram.org ("API development tools"), then on the Pi:

```bash
sudo systemctl stop clubbot
~/clubbot/.venv/bin/pip install telethon
cd ~/clubbot && TELEGRAM_API_ID=... TELEGRAM_API_HASH=... .venv/bin/python scripts/import_rec_members.py
sudo systemctl start clubbot
```

It prints how many it imported vs the chat's member count. If they're far
apart, make the next term's rec chat the last new group (add the bot before
anyone joins). Imported people get the normal grace period from the import day.

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
   `service-account.json`). The weekly database backup DM goes to whoever
   is treasurer, so it follows the handover automatically.
3. Point them at `CLAUDE.md` (project status/decisions) and the design doc in
   `docs/superpowers/specs/`.
