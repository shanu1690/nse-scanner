#!/bin/bash
# MORNING SCAN - emails today's entry list before the market opens (8:45am).
#   chmod +x morning.sh
# Picks are generated from the latest completed close (same signals as the
# evening report), but framed as "what to buy TODAY" with limit-order rules.

cd "$(dirname "$0")"

echo "===== $(date) ====="
.venv/bin/python scanner.py report --email --ntfy --morning >> data/cron.log 2>&1
echo "exit code: $?"
