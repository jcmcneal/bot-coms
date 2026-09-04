#!/usr/bin/env sh
# Invoke from the operator's scheduler every minute. No model polling loop.
set -eu
exec "${BOT_COMS_BOARD_BIN:-bot-coms-board}" reconcile
