#!/bin/bash
# EOD report script - run this daily after market close (e.g. via cron).
# It scans, saves today's picks, emails the report card to you AND pushes
# it to your phone via ntfy.sh.
#
#   chmod +x eod.sh
#   ./eod.sh
#
# NOTE: your Mac must be ON (and awake) at the scheduled time. If it is not,
# move the scanner to an always-on VPS (see README "Run on your phone").

cd "$(dirname "$0")"

echo "===== $(date) ====="
.venv/bin/python scanner.py report --email --ntfy >> data/cron.log 2>&1
echo "exit code: $?"
