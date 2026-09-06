#!/usr/bin/env sh
# Invoke once from the Hermes gateway post-start hook. Do not schedule in cron.
set -eu
exec "${BOT_COMS_BOARD_BIN:-bot-coms-board}" wake
