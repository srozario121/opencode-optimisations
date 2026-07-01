#!/usr/bin/env bash
# item 31.0 — clean-serving T3 isolation runner.
#
# Every T3 measurement in item 31 MUST begin from a confirmed-healthy, freshly
# loaded server with a cleared prefix cache. Item 30 produced TWO contaminated T3
# results from dirty state: (a) a corrupted prefix cache (a 4-layer MTP block
# polluting the 24-layer main cache → invalidate/abort thrash), and (b) a
# server-reload race where opencode never issued a request (the upstream wasn't
# ready). This runner makes the clean cycle structural, not a manual step:
#
#   for each repeat:
#     omlx down → clear ALL prefix caches → omlx up → HEALTH-GATE → run instance
#
# The health-gate (scripts/omlx.sh health) blocks until /v1/models is ready AND a
# tiny warmup completion succeeds through the proxy, so opencode's first call can
# never race a cold upstream. The harness's own mid-run OOM self-heal restart is
# disabled (HARNESS_NO_MIDRUN_RESTART=1) so this wrapper owns the whole lifecycle.
# Each run records the server PID + model-load (process-start) timestamp so any
# later contamination is auditable.
#
# Usage:
#   scripts/run_t3_clean.sh [INSTANCE] [TIMEOUT_S] [REPEATS]
# Env overrides:
#   CONFIG   (baseline)   harness config name
#   LABEL    (auto)       ledger label prefix (per-repeat -rN appended when K>1)
#   BASE_URL (http://127.0.0.1:8080/v1)
#   OMLX_HEALTH_TIMEOUT (300)  seconds the health-gate may wait for first decode
#
# Examples (the 31.2 latency-vs-capability pair):
#   scripts/run_t3_clean.sh sympy__sympy-21627 600
#   scripts/run_t3_clean.sh sympy__sympy-21627 1800
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

INSTANCE="${1:-sympy__sympy-21627}"
TIMEOUT="${2:-600}"
REPEATS="${3:-1}"
CONFIG="${CONFIG:-baseline}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8080/v1}"
LABEL="${LABEL:-clean-t3-${INSTANCE##*-}-${TIMEOUT}s}"

# The wrapper owns serving: omlx backend + no mid-run harness restart.
export HARNESS_SERVE_BACKEND=omlx
export HARNESS_NO_MIDRUN_RESTART=1
# Cold load + first decode is slow (~model-load + ~56s cold prefill, item 30);
# give the health-gate generous headroom so a slow-but-healthy start isn't failed.
export OMLX_HEALTH_TIMEOUT="${OMLX_HEALTH_TIMEOUT:-300}"

RUN_DIR="${OMLX_RUN_DIR:-$HOME/.config/opencode-optimisations}"
SERVER_PID_FILE="$RUN_DIR/omlx-server.pid"
PROXY_PID_FILE="$RUN_DIR/omlx-proxy.pid"
OMLX_SERVER_LOG="$RUN_DIR/omlx-server.log"

mkdir -p scratchpad
LOG="scratchpad/run_t3_clean.out"; : > "$LOG"

say() { echo "$@" | tee -a "$LOG"; }

clear_caches() {
  # Clear EVERY prefix-cache namespace (omlx-cache, omlx-cache-mtp*, -nomtp …) so
  # no stale/incompatible dir from a different serve path can collide (31.0).
  local n=0 d
  for d in "$RUN_DIR"/omlx-cache*; do
    [ -e "$d" ] || continue
    rm -rf "$d" && n=$((n + 1))
  done
  say "  [clean] cleared $n prefix-cache dir(s) under $RUN_DIR"
}

record_server() {
  # Audit trail: server PID + model-load (process-start) timestamp + the omlx log
  # line that announces the loaded model, so contamination is provable after run.
  local spid="(none)" ppid="(none)" lstart="(unknown)"
  [ -f "$SERVER_PID_FILE" ] && spid="$(cat "$SERVER_PID_FILE")"
  [ -f "$PROXY_PID_FILE" ] && ppid="$(cat "$PROXY_PID_FILE")"
  if [ "$spid" != "(none)" ]; then
    lstart="$(ps -o lstart= -p "$spid" 2>/dev/null | sed 's/^ *//')"
    [ -n "$lstart" ] || lstart="(pid $spid not found)"
  fi
  say "  [audit] server-pid=$spid  proxy-pid=$ppid  loaded-at=$lstart"
  if [ -f "$OMLX_SERVER_LOG" ]; then
    grep -iE "loaded|model|ready|listen" "$OMLX_SERVER_LOG" 2>/dev/null | tail -3 \
      | sed 's/^/    /' | tee -a "$LOG" >/dev/null || true
  fi
}

one_run() {
  local label="$1"
  say ""
  say "=== clean cycle for $INSTANCE  label=$label  timeout=${TIMEOUT}s  $(date) ==="

  say "  [clean] omlx down …"
  scripts/omlx.sh down >>"$LOG" 2>&1 || true
  clear_caches

  say "  [clean] omlx up …"
  if ! scripts/omlx.sh up >>"$LOG" 2>&1; then
    say "  ERROR: omlx up failed — see $LOG"; return 1
  fi

  say "  [clean] health-gate (models + warmup completion) …"
  if ! scripts/omlx.sh health >>"$LOG" 2>&1; then
    say "  ERROR: health-gate failed — server not cleanly ready; aborting this run"
    tail -8 "$LOG"
    return 1
  fi
  grep -m1 "health: OK" "$LOG" | sed 's/^/  [clean] /' | tee -a "$LOG" >/dev/null || true
  record_server

  say "  [run] harness_eval.py run --config $CONFIG --instances $INSTANCE --timeout $TIMEOUT"
  uv run python scripts/harness_eval.py run --config "$CONFIG" \
    --instances "$INSTANCE" --label "$label" \
    --timeout "$TIMEOUT" --base-url "$BASE_URL" >>"$LOG" 2>&1
  local rc=$?
  say "  [run] harness exit=$rc"
  return $rc
}

say "=== run_t3_clean: $INSTANCE  config=$CONFIG  timeout=${TIMEOUT}s  repeats=$REPEATS  $(date) ==="
say "    backend=omlx  no-midrun-restart=1  base-url=$BASE_URL"

rc_any=0
if [ "$REPEATS" -le 1 ]; then
  one_run "$LABEL" || rc_any=1
else
  for r in $(seq 1 "$REPEATS"); do
    say ""
    say "########## repeat $r/$REPEATS ##########"
    one_run "${LABEL}-r${r}" || rc_any=1
  done
fi

say ""
say "=== done $(date)  (overall rc=$rc_any) ==="
say "Trajectory + verdict: inspect the ledger / runs dir and $LOG"
exit "$rc_any"
