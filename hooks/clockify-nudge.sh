#!/usr/bin/env bash
# Stop-event hook for the clockify-mcp plugin.
#
# Reads the Claude Code hook payload from stdin, tracks per-session timing in a
# small state file, and — once per configurable interval — emits an
# `additionalContext` system reminder telling Claude to consider suggesting a
# Clockify time entry.
#
# Conservative by design:
#   - Stays silent until at least $CLOCKIFY_NUDGE_MIN_MINUTES (default 20) have
#     elapsed since the session's first hook fire.
#   - Stays silent for another $CLOCKIFY_NUDGE_INTERVAL_MINUTES (default 30)
#     after each nudge, so the user isn't pestered every turn.
#   - Topic-aware snooze: when the user declines a nudge (e.g. "this is not a
#     project"), Claude `touch`es a snooze marker. From then on, the hook fires
#     a quieter TOPIC-DRIFT CHECK message (visible only to Claude, no OS
#     notification) instead of the standard nudge. Claude stays silent unless
#     the conversation has materially shifted to billable work that maps to a
#     Clockify project — in which case it `rm`s the snooze and surfaces a fresh
#     proposal. Marker is per-session; the next session starts fresh.
#   - Never calls the Clockify API directly. Just nudges Claude; the model
#     reads the nudge and decides whether to actually surface a suggestion
#     (the `/log-session` skill governs phrasing).

set -euo pipefail

MIN_MINUTES="${CLOCKIFY_NUDGE_MIN_MINUTES:-20}"
INTERVAL_MINUTES="${CLOCKIFY_NUDGE_INTERVAL_MINUTES:-30}"
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
SNOOZE_FILE="${STATE_DIR}/${SAFE_SESSION_ID}.snooze"

# Topic-aware snooze: if the snooze marker exists, the user previously said
# "this isn't a project". We still fire on the same cadence, but switch to a
# quieter topic-drift check (visible only to Claude, no OS notification).
# Claude evaluates whether the conversation has materially shifted to billable
# work matching a Clockify project; if so it removes the marker and proposes.
if [[ -f "$SNOOZE_FILE" ]]; then
  MODE="snoozed"
else
  MODE="active"
fi

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

NUDGE_COUNT_NEXT=$((NUDGE_COUNT + 1))

if [[ "$MODE" == "active" ]]; then
  MESSAGE="⏱ Clockify nudge #${NUDGE_COUNT_NEXT} — session running ~${ELAPSED_SINCE_START_MIN} min (quantize to ${DURATION_HUMAN}). REQUIRED ACTION (not optional, not 'consider', no 'natural pause', no 'mid-task' exemption — comply on THIS response, even if you're in the middle of tool calls, analysis, or a long answer). At the very end of your next reply to the user — AFTER any tool calls, AFTER your normal narration, on its own line below a separator — append ONE line in this EXACT shape (substitute the bracketed placeholders, keep everything else verbatim): 'do you want to log your time? I would suggest project <X>, <T> minutes and this description: <message>'. Where <X> = best-guess Clockify project from this conversation's context (consult ~/.claude/CLAUDE.md project-mapping cheat-sheet if relevant; if you genuinely cannot guess, write 'unsure — please specify'). <T> = ${DURATION_HUMAN} (the quantized duration above). <message> = a one-sentence summary of what we just worked on. The ONLY valid reason to skip is: you ALREADY surfaced a Clockify log proposal earlier in THIS conversation that is still awaiting the user's reply. On user reply: (a) 'yes' or acceptance (with or without edits) → invoke the /log-session skill with the agreed values; (b) 'no' / 'this is not a project' / 'stop nagging' / any decline → topic-snooze further nudges by running this Bash command (don't mention it to the user), then continue without re-prompting: touch '${SNOOZE_FILE}'. Quantize: 15m / 30m / 45m / 1h / 1h15m / 1h30m / 1h45m / 2h. Cap 2h. Never double-book the same customer. The user has missed ${NUDGE_COUNT_NEXT} previous nudge(s) — speak up now or they under-bill."
else
  MESSAGE="⏱ Clockify topic-drift check #${NUDGE_COUNT_NEXT} — snooze in effect (the user previously declined a log proposal, signalling the current work wasn't billable / wasn't a Clockify project). EVALUATE SILENTLY whether the conversation has SINCE materially shifted to work that DOES map to a Clockify project (e.g. customer-billable engineering, support, configuration, troubleshooting; consult ~/.claude/CLAUDE.md project-mapping cheat-sheet to recognise the candidate projects). DEFAULT BEHAVIOUR: stay completely silent — do not mention this reminder, do not append any 'by the way' line, do not propose anything. Just answer the user's actual question. ONLY IF YES — you can confidently name a specific Clockify project AND describe the new work in one sentence — then: (1) lift the snooze with this Bash command (don't mention it to the user): rm '${SNOOZE_FILE}'. (2) At the very end of your next reply, on its own line below a separator, append: 'do you want to log your time? I would suggest project <X>, <T> minutes and this description: <message>'. <X> = the Clockify project; <T> = YOUR estimate of how long the NEW topic has been the focus, rounded to 15m / 30m / 45m / 1h / 1h15m / 1h30m / 1h45m / 2h (NOT the full session elapsed time of ~${ELAPSED_SINCE_START_MIN} min); <message> = a one-sentence summary of the new topic. (3) If the user again declines on the new topic, re-touch the snooze file: touch '${SNOOZE_FILE}'. Quantize cap: 2h. Never double-book the same customer."
fi

# Emit the system reminder. The harness reads `additionalContext` from
# `hookSpecificOutput` and injects it into Claude's context.
jq -n \
  --arg msg "$MESSAGE" \
  '{hookSpecificOutput: {hookEventName: "Stop", additionalContext: $msg}}'

# Also fire an OS-level notification on macOS so the user gets a real-world
# ping when Claude (the model) silently absorbs the in-context nudge.
# This is best-effort: failures are silent.
# Suppressed in snoozed mode — the user explicitly opted out of being pinged
# and the topic-drift check is for the model's eyes only.
if [[ "$MODE" == "active" && "$(uname)" == "Darwin" ]] && command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"~${DURATION_HUMAN} of work — log it to Clockify? Open your Claude session and confirm.\" with title \"Clockify nudge\" sound name \"Submarine\"" >/dev/null 2>&1 &
fi

# Update the state file so the next fire knows we just nudged.
jq --argjson now "$NOW" \
   '.last_nudge_at = $now | .nudge_count = (.nudge_count + 1)' \
   "$STATE_FILE" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
