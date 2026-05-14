#!/usr/bin/env bash
# Stop-event hook for the clockify-mcp plugin.
#
# Reads the Claude Code hook payload from stdin, tracks per-session timing in a
# small state file, and — once per configurable interval — emits an
# `additionalContext` system reminder telling Claude to consider suggesting a
# Clockify time entry.
#
# Conservative by design:
#   - Stays silent until at least $CLOCKIFY_NUDGE_MIN_MINUTES (default 30) have
#     elapsed since the session's first hook fire.
#   - Stays silent for another $CLOCKIFY_NUDGE_INTERVAL_MINUTES (default 45)
#     after each nudge, so the user isn't pestered every turn.
#   - Never calls the Clockify API directly. Just nudges Claude; the model
#     reads the nudge and decides whether to actually surface a suggestion
#     (the `/log-session` skill governs phrasing).

set -euo pipefail

MIN_MINUTES="${CLOCKIFY_NUDGE_MIN_MINUTES:-30}"
INTERVAL_MINUTES="${CLOCKIFY_NUDGE_INTERVAL_MINUTES:-45}"
STATE_DIR="${CLOCKIFY_NUDGE_STATE_DIR:-${HOME}/.local/state/clockify-mcp}"

mkdir -p "$STATE_DIR"

# Read the hook payload from stdin. Claude Code passes a JSON object that
# includes `session_id` and `hook_event_name`. We only act on `Stop` events.
PAYLOAD="$(cat || true)"

# Defensive: if jq isn't installed or the payload is malformed, exit silently
# rather than breaking the user's session.
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

SESSION_ID="$(echo "$PAYLOAD" | jq -r '.session_id // empty')"
EVENT_NAME="$(echo "$PAYLOAD" | jq -r '.hook_event_name // empty')"

# Only run on Stop events; never block on anything else.
if [[ "$EVENT_NAME" != "Stop" ]] || [[ -z "$SESSION_ID" ]]; then
  exit 0
fi

# Sanitise the session id (alphanumerics + dashes only) so it's safe to use as a filename.
SAFE_SESSION_ID="$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9._-' '_')"
STATE_FILE="${STATE_DIR}/${SAFE_SESSION_ID}.json"

NOW="$(date +%s)"

if [[ ! -f "$STATE_FILE" ]]; then
  jq -n --argjson now "$NOW" \
    '{started_at: $now, last_nudge_at: $now, nudge_count: 0}' \
    > "$STATE_FILE"
  # First hook fire of this session — no nudge yet.
  exit 0
fi

STARTED_AT="$(jq -r '.started_at' "$STATE_FILE")"
LAST_NUDGE_AT="$(jq -r '.last_nudge_at' "$STATE_FILE")"
NUDGE_COUNT="$(jq -r '.nudge_count' "$STATE_FILE")"

ELAPSED_SINCE_START_MIN=$(( (NOW - STARTED_AT) / 60 ))
ELAPSED_SINCE_LAST_NUDGE_MIN=$(( (NOW - LAST_NUDGE_AT) / 60 ))

# Quiet during the warm-up window, and between nudges.
if (( ELAPSED_SINCE_START_MIN < MIN_MINUTES )); then
  exit 0
fi
if (( NUDGE_COUNT > 0 )) && (( ELAPSED_SINCE_LAST_NUDGE_MIN < INTERVAL_MINUTES )); then
  exit 0
fi

# Round the wall-clock minutes down to the nearest 15-min quantum so the model
# doesn't have to do arithmetic to honour the project's quantization rule.
QUANTIZED_MIN=$(( (ELAPSED_SINCE_START_MIN / 15) * 15 ))
if (( QUANTIZED_MIN < 15 )); then
  QUANTIZED_MIN=15
fi
HOURS=$(( QUANTIZED_MIN / 60 ))
MINS=$(( QUANTIZED_MIN % 60 ))
if (( HOURS == 0 )); then
  DURATION_HUMAN="${MINS}m"
elif (( MINS == 0 )); then
  DURATION_HUMAN="${HOURS}h"
else
  DURATION_HUMAN="${HOURS}h${MINS}m"
fi

MESSAGE="⏱ Clockify nudge: this Claude Code session has been running for about ${ELAPSED_SINCE_START_MIN} min (quantized: ${DURATION_HUMAN}). If a coherent chunk of work just completed, consider asking the user whether to file a Clockify entry. Follow the /log-session skill rules: 15-min increments only (15m, 30m, 45m, 1h, 1h15m, 1h30m, 1h45m, 2h); single entry ≤ 2h (prefer ≤ 1h30m); split longer work into chunks; never double-book the same customer. Don't interrupt mid-task; if you already proposed a log this session, stay quiet."

# Emit the system reminder. The harness reads `additionalContext` from
# `hookSpecificOutput` and injects it into Claude's context.
jq -n \
  --arg msg "$MESSAGE" \
  '{hookSpecificOutput: {hookEventName: "Stop", additionalContext: $msg}}'

# Update the state file so the next fire knows we just nudged.
jq --argjson now "$NOW" \
   '.last_nudge_at = $now | .nudge_count = (.nudge_count + 1)' \
   "$STATE_FILE" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
