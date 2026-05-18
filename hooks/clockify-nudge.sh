#!/usr/bin/env bash
# clockify-nudge.sh — multi-event hook for the clockify-mcp plugin.
#
# This single script is wired up to THREE Claude Code hook events and
# dispatches on `hook_event_name`:
#
#   UserPromptSubmit  → accumulates the THINK/TYPE gap (capped at
#                       $CLOCKIFY_THINK_CAP_SEC, default 420 s = 7 min) into
#                       the session's `active_sec` counter, then — if the
#                       cadence allows — emits the nudge `additionalContext`
#                       telling Claude to surface a log proposal.
#
#   Stop              → accumulates the GENERATE segment (uncapped: while
#                       Claude is producing tokens / running tool calls, the
#                       user is engaged) into `active_sec`. Silent — no
#                       nudge, no output.
#
#   PostToolUse       → matched on `mcp__*clockify*__add_time_entry` tool
#                       calls only. Resets the active-time counter, the
#                       nudge counter, and lifts any session snooze. Silent.
#
# Active-time accounting
# ----------------------
# Per turn we accumulate two segments into `active_sec` (stored in the
# per-session JSON state file):
#
#   active += min(T_user_N - T_stop_{N-1}, THINK_CAP_SEC)   ← capped think/type
#   active += T_stop_N - T_user_N                            ← full generate
#
# Wall-clock time spent AFK does not count. The duration suggested in nudges
# is derived from `active_sec`, not from wall clock — and is hard-clamped to
# the 2 h policy cap inside this script (the cap is no longer reliant on the
# LLM reading and honouring it from prose).
#
# Cadence
# -------
#   - First nudge fires once `active_sec` ≥ CLOCKIFY_NUDGE_MIN_MINUTES
#     (default 20).
#   - Subsequent nudges fire once another CLOCKIFY_NUDGE_INTERVAL_MINUTES
#     (default 30) of active time have accrued since the previous nudge.
#
# Reset on log
# ------------
# When the user files a time entry via the clockify MCP, the PostToolUse
# branch zeroes out `active_sec`, `active_sec_at_last_nudge`, and
# `nudge_count`, and removes the snooze marker if present. The next nudge
# therefore needs another full warm-up window of fresh active work.
#
# Snooze
# ------
# If `${session}.snooze` exists, the active-mode nudge is replaced by a
# quieter topic-drift check that defaults to staying silent. Created by
# Claude on user decline; lifted automatically on the next log.
#
# No OS notifications anywhere.

set -euo pipefail

MIN_MINUTES="${CLOCKIFY_NUDGE_MIN_MINUTES:-20}"
INTERVAL_MINUTES="${CLOCKIFY_NUDGE_INTERVAL_MINUTES:-30}"
THINK_CAP_SEC="${CLOCKIFY_THINK_CAP_SEC:-420}"
CAP_MIN="${CLOCKIFY_NUDGE_CAP_MIN:-120}"  # hard 2 h cap on suggested duration
STATE_DIR="${CLOCKIFY_NUDGE_STATE_DIR:-${HOME}/.local/state/clockify-mcp}"

mkdir -p "$STATE_DIR"

# Read the hook payload from stdin. Claude Code passes a JSON object that
# includes `session_id`, `hook_event_name`, and (on PostToolUse) `tool_name`.
PAYLOAD="$(cat || true)"

# Defensive: if jq isn't installed or the payload is malformed, exit silently
# rather than breaking the user's session.
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty')"
EVENT_NAME="$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // empty')"
TOOL_NAME="$(printf '%s' "$PAYLOAD" | jq -r '.tool_name // empty')"

[[ -z "$SESSION_ID" ]] && exit 0

# Sanitise the session id (alphanumerics + dashes only) so it's safe to use as a filename.
SAFE_SESSION_ID="$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9._-' '_')"
STATE_FILE="${STATE_DIR}/${SAFE_SESSION_ID}.json"
SNOOZE_FILE="${STATE_DIR}/${SAFE_SESSION_ID}.snooze"

NOW="$(date +%s)"

# Initialise (or migrate) state file. We detect a pre-0.7 state file by the
# absence of the `active_sec` key and reset rather than trying to migrate the
# old wall-clock counters.
if [[ ! -f "$STATE_FILE" ]] || ! jq -e 'has("active_sec")' "$STATE_FILE" >/dev/null 2>&1; then
  jq -n '{
    last_user_at: null,
    last_stop_at: null,
    active_sec: 0,
    active_sec_at_last_nudge: 0,
    nudge_count: 0
  }' > "$STATE_FILE"
fi

# Read/write helpers.
read_state() { jq -r ".$1 // empty" "$STATE_FILE"; }
write_state() {
  jq "$1" "$STATE_FILE" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

case "$EVENT_NAME" in
  UserPromptSubmit)
    # 1) Accumulate the capped THINK/TYPE gap since the previous Stop.
    LAST_STOP_AT="$(read_state last_stop_at)"
    if [[ -n "$LAST_STOP_AT" && "$LAST_STOP_AT" != "null" ]]; then
      GAP=$(( NOW - LAST_STOP_AT ))
      (( GAP < 0 )) && GAP=0
      (( GAP > THINK_CAP_SEC )) && GAP=$THINK_CAP_SEC
    else
      GAP=0
    fi
    write_state "(.active_sec |= ((. // 0) + ${GAP})) | (.last_user_at = ${NOW})"

    # 2) Cadence check, based on active time.
    ACTIVE_SEC="$(read_state active_sec)"
    ACTIVE_AT_LAST="$(read_state active_sec_at_last_nudge)"
    NUDGE_COUNT="$(read_state nudge_count)"
    ACTIVE_SEC=${ACTIVE_SEC:-0}
    ACTIVE_AT_LAST=${ACTIVE_AT_LAST:-0}
    NUDGE_COUNT=${NUDGE_COUNT:-0}
    ACTIVE_MIN=$(( ACTIVE_SEC / 60 ))
    NEW_ACTIVE_MIN=$(( (ACTIVE_SEC - ACTIVE_AT_LAST) / 60 ))

    if (( ACTIVE_MIN < MIN_MINUTES )); then
      exit 0
    fi
    if (( NUDGE_COUNT > 0 )) && (( NEW_ACTIVE_MIN < INTERVAL_MINUTES )); then
      exit 0
    fi

    # 3) Quantize to a 15-min boundary, floor 15, cap at CAP_MIN (2 h).
    QUANTIZED_MIN=$(( (ACTIVE_MIN / 15) * 15 ))
    (( QUANTIZED_MIN < 15 )) && QUANTIZED_MIN=15
    (( QUANTIZED_MIN > CAP_MIN )) && QUANTIZED_MIN=$CAP_MIN
    HOURS=$(( QUANTIZED_MIN / 60 ))
    MINS=$(( QUANTIZED_MIN % 60 ))
    if (( HOURS == 0 )); then DURATION_HUMAN="${MINS}m"
    elif (( MINS == 0 )); then DURATION_HUMAN="${HOURS}h"
    else DURATION_HUMAN="${HOURS}h${MINS}m"
    fi

    # 4) Compose the nudge message.
    if [[ -f "$SNOOZE_FILE" ]]; then MODE="snoozed"; else MODE="active"; fi
    NUDGE_COUNT_NEXT=$((NUDGE_COUNT + 1))

    if [[ "$MODE" == "active" ]]; then
      MESSAGE="⏱ Clockify nudge #${NUDGE_COUNT_NEXT} — ${ACTIVE_MIN} min of active work since the last log (quantized to ${DURATION_HUMAN}, capped at 2h by policy). REQUIRED ACTION (not optional, not 'consider', no 'natural pause', no 'mid-task' exemption — comply on THIS response, even if you're in the middle of tool calls, analysis, or a long answer). At the very end of your next reply to the user — AFTER any tool calls, AFTER your normal narration, on its own line below a separator — append ONE line in this EXACT shape (substitute the bracketed placeholders, keep everything else verbatim): 'do you want to log your time? I would suggest project <X>, <T> minutes and this description: <message>'. Where <X> = best-guess Clockify project from this conversation's context (consult ~/.claude/CLAUDE.md project-mapping cheat-sheet if relevant; if you genuinely cannot guess, write 'unsure — please specify'). <T> = ${DURATION_HUMAN} (the quantized active time above). <message> = a one-sentence summary of what we just worked on. The ONLY valid reason to skip is: you ALREADY surfaced a Clockify log proposal earlier in THIS conversation that is still awaiting the user's reply. On user reply: (a) 'yes' or acceptance (with or without edits) → invoke the /log-session skill with the agreed values (the active-time counter resets to zero automatically once the entry is filed); (b) 'no' / 'this is not a project' / 'stop nagging' / any decline → topic-snooze further nudges by running this Bash command (don't mention it to the user), then continue without re-prompting: touch '${SNOOZE_FILE}'. Quantize: 15m / 30m / 45m / 1h / 1h15m / 1h30m / 1h45m / 2h. Never double-book the same customer."
    else
      MESSAGE="⏱ Clockify topic-drift check #${NUDGE_COUNT_NEXT} — snooze in effect (the user previously declined a log proposal, signalling the current work wasn't billable / wasn't a Clockify project). EVALUATE SILENTLY whether the conversation has SINCE materially shifted to work that DOES map to a Clockify project (e.g. customer-billable engineering, support, configuration, troubleshooting; consult ~/.claude/CLAUDE.md project-mapping cheat-sheet to recognise the candidate projects). DEFAULT BEHAVIOUR: stay completely silent — do not mention this reminder, do not append any 'by the way' line, do not propose anything. Just answer the user's actual question. ONLY IF YES — you can confidently name a specific Clockify project AND describe the new work in one sentence — then: (1) lift the snooze with this Bash command (don't mention it to the user): rm '${SNOOZE_FILE}'. (2) At the very end of your next reply, on its own line below a separator, append: 'do you want to log your time? I would suggest project <X>, <T> minutes and this description: <message>'. <X> = the Clockify project; <T> = YOUR estimate of how long the NEW topic has been the focus, rounded to 15m / 30m / 45m / 1h / 1h15m / 1h30m / 1h45m / 2h (active-time accounting suggests ~${ACTIVE_MIN} min total since the last log, but if the new topic only covers part of that, use your judgement); <message> = a one-sentence summary of the new topic. (3) If the user again declines on the new topic, re-touch the snooze file: touch '${SNOOZE_FILE}'. Quantize cap: 2h. Never double-book the same customer."
    fi

    # 5) Emit the system reminder and record that we nudged.
    jq -n --arg msg "$MESSAGE" \
      '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $msg}}'

    write_state "(.active_sec_at_last_nudge = ${ACTIVE_SEC}) | (.nudge_count = (.nudge_count // 0) + 1)"
    ;;

  Stop)
    # Silent — accumulate the GENERATE segment (T_user_N → T_stop_N).
    LAST_USER_AT="$(read_state last_user_at)"
    if [[ -n "$LAST_USER_AT" && "$LAST_USER_AT" != "null" ]]; then
      GEN=$(( NOW - LAST_USER_AT ))
      (( GEN < 0 )) && GEN=0
    else
      GEN=0
    fi
    write_state "(.active_sec |= ((. // 0) + ${GEN})) | (.last_stop_at = ${NOW})"
    ;;

  PostToolUse)
    # Silent — reset on add_time_entry. We filter inside the script as a
    # defensive fallback in case the hooks.json regex over-matches.
    if [[ "$TOOL_NAME" =~ clockify.*add_time_entry ]]; then
      write_state ".active_sec = 0 | .active_sec_at_last_nudge = 0 | .nudge_count = 0"
      rm -f "$SNOOZE_FILE"
    fi
    ;;
esac
