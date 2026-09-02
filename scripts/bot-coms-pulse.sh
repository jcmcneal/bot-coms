#!/usr/bin/env bash
# bot-coms-pulse.sh — stuck-lease reclaim stub (NOT the control plane).
#
# Reply-stack doorbells fire after successful spool put (see bot_coms.doorbell).
# Disable the old 2-minute LaunchAgent/cron job id ``b0tc0mspu15e``:
#   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/…b0tc0mspu15e…  # if loaded
#   # or remove/disable the cron/launchd plist that invoked this script every 2m
#
# This script only reclaims stale processing leases so a crashed worker can
# recover without waiting for fresh traffic. It does not wake Hermes, notify
# Discord, or walk an org chart.
set -eu
ROOT="${BOT_COMS_SPOOL_ROOT:-$HOME/.hermes/team/spool}"
COMS="${BOT_COMS_BIN:-bot-coms}"
PEERS_YAML="${BOT_COMS_PEERS_YAML:-$HOME/.hermes/team/peers.yaml}"

FALLBACK_PEERS="pm
swe
verifier
dna-researcher
em"

load_peer_ids() {
  if [[ ! -f "$PEERS_YAML" ]]; then
    return 1
  fi
  python3 -c '
import sys
path = sys.argv[1]
try:
    import yaml
    data = yaml.safe_load(open(path)) or {}
except Exception:
    sys.exit(1)
peers = data.get("peers") or []
n = 0
for p in peers:
    if not isinstance(p, dict):
        continue
    pid = str(p.get("id") or "").strip()
    if pid:
        sys.stdout.write(pid + "\n")
        n += 1
sys.exit(0 if n else 1)
' "$PEERS_YAML" 2>/dev/null
}

PEERS="$(load_peer_ids)" || PEERS="$FALLBACK_PEERS"

if [[ -n "${BOT_COMS_PULSE_ONLY:-}" ]]; then
  PEERS="${BOT_COMS_PULSE_ONLY}"
fi

while IFS= read -r peer; do
  [[ -n "$peer" ]] || continue
  [[ -d "$ROOT/$peer" ]] || continue
  export BOT_COMS_SPOOL_ROOT="$ROOT"
  export BOT_COMS_PEER_ID="$peer"
  "$COMS" reclaim >/dev/null 2>&1 || true
done <<PEERS_EOF
$PEERS
PEERS_EOF
exit 0
