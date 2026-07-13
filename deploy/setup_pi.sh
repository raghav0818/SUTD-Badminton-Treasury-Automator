#!/bin/bash
# Sets up — or updates — the club bot on the Raspberry Pi.
#   Run on the Pi:  bash ~/clubbot/deploy/setup_pi.sh
# Safe to re-run any time; use it again after every code update.
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$PWD"

if [ ! -f .env ]; then
    echo "ERROR: no .env file in $APP_DIR — copy it over from your PC first." >&2
    exit 1
fi

echo "==> Creating Python environment and installing dependencies (first run takes a few minutes)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade -r requirements.txt

echo "==> Installing the systemd service (may ask for your password)"
sed "s|{{USER}}|$USER|g; s|{{DIR}}|$APP_DIR|g" deploy/clubbot.service |
    sudo tee /etc/systemd/system/clubbot.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable clubbot      # start automatically on every boot
sudo systemctl restart clubbot     # (re)start it now

sleep 3
sudo systemctl --no-pager --lines=5 status clubbot || true
echo
echo "Done. The bot restarts itself on crashes and on reboot."
echo "Watch live logs with:   journalctl -u clubbot -f      (Ctrl+C to stop watching)"
