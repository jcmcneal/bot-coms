#!/usr/bin/env bash
# bot-coms-pulse.sh — assign wakes Hermes; report/report_only/cancel → board worker;
# PM type=response + notifiable headers.source → bot-coms notify worker.
# Peer roster: ~/.hermes/team/peers.yaml (CoS-owned; hermes-team updates it).
set -eu
ROOT="${BOT_COMS_SPOOL_ROOT:-$HOME/.hermes/team/spool}"
BOARD="${BOT_COMS_BOARD_BIN:-bot-coms-board}"
COMS="${BOT_COMS_BIN:-bot-coms}"
HERMES="${HERMES_BIN:-$HOME/.local/bin/hermes}"
TEAM_CTX="${BOT_COMS_TEAM_ROOT:-$HOME/.hermes/team}/context"
PEERS_YAML="${BOT_COMS_PEERS_YAML:-$HOME/.hermes/team/peers.yaml}"

# Fallback only if peers.yaml is missing or unreadable — original four.
FALLBACK_ROSTER="pm	project-manager
swe	software-engineer
verifier	verifier
dna-researcher	dna-researcher"

load_roster() {
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
    prof = str(p.get("profile") or "").strip()
    if pid and prof:
        sys.stdout.write(pid + "\t" + prof + "\n")
        n += 1
sys.exit(0 if n else 1)
' "$PEERS_YAML" 2>/dev/null
}

ROSTER="$(load_roster)" || ROSTER="$FALLBACK_ROSTER"

profile_for_peer() {
  local id prof
  while IFS="$(printf '\t')" read -r id prof; do
    [[ -n "$id" ]] || continue
    if [[ "$id" == "$1" ]]; then
      printf '%s\n' "$prof"
      return 0
    fi
  done <<ROSTER_EOF
$ROSTER
ROSTER_EOF
  return 1
}

reclaim_peer() {
  local peer="$1"
  export BOT_COMS_SPOOL_ROOT="$ROOT"
  export BOT_COMS_PEER_ID="$peer"
  "$COMS" reclaim >/dev/null 2>&1 || true
}

is_notifiable_response() {
  local mail="$1"
  local typ source platform
  typ=$(jq -r '.type // empty' "$mail" 2>/dev/null || true)
  [[ "$typ" == "response" ]] || return 1
  source=$(jq -r '.headers.source // empty' "$mail" 2>/dev/null || true)
  [[ -n "$source" ]] || return 1
  platform="${source%%:*}"
  case "$platform" in
    ''|cli|tui|local) return 1 ;;
    *) return 0 ;;
  esac
}

run_notify_worker() {
  local peer="$1"
  export BOT_COMS_SPOOL_ROOT="$ROOT"
  export BOT_COMS_PEER_ID="$peer"
  "$COMS" worker --peer "$peer" --handler bot_coms.notify:source_argv \
    --idle-rounds 3 >/dev/null 2>&1 || true
}

wake_assign() {
  local peer="$1" profile="$2" mail="$3"
  local intent slice assignment_path qf
  intent=$(jq -r '.payload.intent // empty' "$mail" 2>/dev/null || true)
  [[ "$intent" == "assign" ]] || return 1
  slice=$(jq -r '.payload.slice // empty' "$mail" 2>/dev/null || true)
  [[ -n "$slice" ]] || return 1
  assignment_path="$TEAM_CTX/${slice}.md"
  qf="$HOME/.hermes/profiles/$profile/board-wake-$$.txt"
  mkdir -p "$(dirname "$qf")"
  {
    printf 'Slice %s assigned.\n' "$slice"
    printf 'Assignment: %s\n\n' "$assignment_path"
    if [[ -f "$assignment_path" ]]; then
      head -c 4000 "$assignment_path"
      printf '\n\n'
    fi
    cat <<'EOF'
Call team_inbox, then launch ONE cursor_screen job for this slice.
Stamp team_bus status RUNNING with active_job, then bot_coms_ack. End turn after launch.
EOF
  } >"$qf"
  if [[ "$profile" == "default" ]]; then
    "$HERMES" chat --in ~ -c "Assign ${slice}" --create-if-missing -Q --query-file "$qf" \
      >/dev/null 2>&1 &
  else
    "$HERMES" -p "$profile" chat --in ~ -c "Assign ${slice}" --create-if-missing -Q --query-file "$qf" \
      >/dev/null 2>&1 &
  fi
  return 0
}

peers=()
while IFS="$(printf '\t')" read -r id _prof; do
  [[ -n "$id" ]] && peers+=("$id")
done <<PEERS_EOF
$ROSTER
PEERS_EOF

if [[ -n "${BOT_COMS_PULSE_ONLY:-}" ]]; then
  peers=("${BOT_COMS_PULSE_ONLY}")
fi

now=$(date +%s)
for peer in "${peers[@]}"; do
  inbox="$ROOT/$peer/inbox"
  [[ -d "$inbox" ]] || continue
  mail=$(find "$inbox" -maxdepth 1 -name '*.json' -type f 2>/dev/null | head -n 1)
  [[ -n "$mail" ]] || continue
  stamp="$ROOT/$peer/state/pulse.last"
  mkdir -p "$ROOT/$peer/state"
  last=0
  [[ -f "$stamp" ]] && last=$(cat "$stamp" 2>/dev/null || echo 0)
  if (( now - last < 90 )); then
    continue
  fi
  echo "$now" > "$stamp"
  profile=$(profile_for_peer "$peer") || continue
  export BOT_COMS_SPOOL_ROOT="$ROOT"
  export BOT_COMS_PEER_ID="$peer"
  reclaim_peer "$peer"

  if [[ "$peer" == "pm" ]]; then
    if is_notifiable_response "$mail"; then
      run_notify_worker "$peer"
      continue
    fi
    if wake_assign "$peer" "$profile" "$mail"; then
      continue
    fi
    "$BOARD" worker --peer "$peer" --idle-rounds 3 >/dev/null 2>&1 || true
    mail=$(find "$inbox" -maxdepth 1 -name '*.json' -type f 2>/dev/null | head -n 1)
    if [[ -n "$mail" ]] && is_notifiable_response "$mail"; then
      run_notify_worker "$peer"
    fi
    continue
  fi

  if wake_assign "$peer" "$profile" "$mail"; then
    continue
  fi
  "$BOARD" worker --peer "$peer" --idle-rounds 3 >/dev/null 2>&1 || true
done
exit 0
