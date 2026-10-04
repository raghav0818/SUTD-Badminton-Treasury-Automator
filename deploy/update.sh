#!/bin/bash
# Pulls the latest code from GitHub and restarts the bot.
#   Run on the Pi:  bash ~/clubbot/deploy/update.sh
# Your .env, service-account.json and clubbot.db are not in git, so they are
# never touched.
set -euo pipefail

# Wrapped in a function so bash reads the whole script before running it:
# `git pull` may replace this very file mid-run.
main() {
    cd "$(dirname "$0")/.."
    echo "==> Downloading the latest code"
    git pull --ff-only
    bash deploy/setup_pi.sh
}

main "$@"
exit
