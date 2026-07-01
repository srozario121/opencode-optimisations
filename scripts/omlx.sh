#!/usr/bin/env bash
# item 30 — omlx serving controller (mirrors scripts/mlx.sh for the omlx backend).
#
# Two-process topology, identical in shape to mlx.sh so the opencode config never
# changes when you swap backends:
#   opencode -> proxy :8080 (scripts/omlx_repair_proxy.py) -> omlx :8088
# The repair proxy recovers omlx's gemma4 output defects (eos-leak + text
# tool-calls; item 29/30). Disable it with OMLX_PROXY=0 (opencode then talks
# straight to omlx, which regresses tool-call success — see TODO item 30).
#
# Serving stays byte-identical to mlx-lm: omlx loads the SAME pinned
# gemma-4-E4B-it-qat-4bit safetensors mlx.sh serves, via a dedicated single-model
# dir that symlinks to $MLX_MODELS_DIR/<model> (item 24's no-model-swap closure
# holds — verify with the sha256 manifest).
#
# Offline / 16 GB-fit (the harness's hard gate): omlx is a brew-vendored binary
# (no PyPI egress at serve time), the model is local, and HF egress is poisoned
# (--hf-endpoint blackhole + --no-hf-cache + HF_HUB_OFFLINE=1 + cleared proxies).
# Validated by the blocked-egress smoke (item 29 stage 1d). A backend that cannot
# serve under blocked egress is an automatic reject.
#
#   scripts/omlx.sh up|down|status|serve|pull
#
# Knobs (env): OMLX_PORT (8080 front), OMLX_UPSTREAM_PORT (8088 omlx),
#   OMLX_PROXY (1), OMLX_PROXY_NO_THINK (0), OMLX_PROXY_CAPTURE (''),
#   OMLX_MEMORY_GUARD (safe), OMLX_CACHE (0 => --no-cache; 1 => tiered cache with
#   caps — see the cache/stability tradeoff in TODO item 30), OMLX_HOT_CACHE_MAX
#   (auto), OMLX_MTP (1 => write the vlm_mtp drafter block; needs the drafter
#   pulled), OMLX_MTP_BLOCK_SIZE (3), OMLX_PYTHON (auto — avoids the broken-pyenv
#   wedge; the proxy is pure stdlib so any 3.9+ works).
set -euo pipefail

DEFAULT_MODEL="gemma-4-E4B-it-qat-4bit"
MODEL="${OMLX_MODEL:-$DEFAULT_MODEL}"

PORT="${OMLX_PORT:-8080}"                       # front port opencode talks to
OMLX_PROXY="${OMLX_PROXY:-1}"
UPSTREAM_PORT="${OMLX_UPSTREAM_PORT:-8088}"     # the omlx server
if [ "$OMLX_PROXY" != "0" ]; then SERVE_PORT="$UPSTREAM_PORT"; else SERVE_PORT="$PORT"; fi

RUN_DIR="${OMLX_RUN_DIR:-$HOME/.config/opencode-optimisations}"

MEMORY_GUARD="${OMLX_MEMORY_GUARD:-safe}"
# Prefix cache ON by default (item 30): the agentic loop re-sends a ~3 k-token
# system-prompt prefix every turn, so caching it cuts warm re-prefill ~5.6×
# (item 29). The earlier cache-ON OOM was driven by temp-1.0 narration bloating
# contexts; greedy temp-parity removes that. Bounded: SSD prefix cache + capped
# hot RAM cache + safe memory guard. Set OMLX_CACHE=0 for the --no-cache anchor.
# Sweep-tuned defaults (item 30 cache sweep): prefix cache gives ~27× warm
# re-prefill, lossless, 0 rejections. hot-cache SIZE barely matters (SSD-only ≈
# 4GB), so keep it small (2GB) for 16GB-fit headroom; initial-cache-blocks=512
# is the best single lever (+22% → ~33× warm) at the cost of a slower cold start;
# memory-guard=balanced gave no gain → keep safe. MTP coexists cleanly.
CACHE="${OMLX_CACHE:-1}"
HOT_CACHE_MAX="${OMLX_HOT_CACHE_MAX:-2GB}"      # in-RAM hot cache cap (OOM lever; size non-critical)
SSD_CACHE_MAX="${OMLX_SSD_CACHE_MAX:-}"         # default omlx 100GB
INITIAL_CACHE_BLOCKS="${OMLX_INITIAL_CACHE_BLOCKS:-512}"  # +22% warm vs omlx default 256
MTP="${OMLX_MTP:-1}"
MTP_BLOCK_SIZE="${OMLX_MTP_BLOCK_SIZE:-3}"
# Prefix-cache dir NAMESPACED by the MTP layer config (item 31 infra fix). The
# VLM-MTP drafter is a 4-layer head; the main gemma-4 model is 24 layers. A prefix
# cache written by one serve config and re-read by another with a different layer
# count triggers omlx's "Cache layer count mismatch: block has 4 layers, expected
# 24" → invalidate/abort thrash (the contaminated 1800s T3 run, item 30). Keying
# the dir by mtp-on/off + block size guarantees configs never share a cache, so a
# stale dir from a different serve path can't collide. (Within a single serve the
# drafter/main keyspace is omlx's own concern; the clean wrapper also clears the
# dir per run.) An explicit OMLX_SSD_CACHE_DIR override is used verbatim.
if [ "$MTP" = "0" ]; then _CACHE_NS="nomtp"; else _CACHE_NS="mtp${MTP_BLOCK_SIZE}"; fi
SSD_CACHE_DIR="${OMLX_SSD_CACHE_DIR:-$RUN_DIR/omlx-cache-$_CACHE_NS}"
# Dedicated single-model dir (omlx discovers EVERY subdir as a model; the shared
# mlx-models dir also holds Qwen3.5 from item 24, so point omlx at a clean dir
# that symlinks ONLY the pinned gemma — byte-identical weights, no extra models).
MODELS_DIR="${OMLX_MODELS_DIR:-$RUN_DIR/omlx-models}"
MLX_MODELS_DIR="${MLX_MODELS_DIR:-$HOME/.config/opencode-optimisations/mlx-models}"
MLX_TARGET="$MLX_MODELS_DIR/$MODEL"
TARGET="$MODELS_DIR/$MODEL"

# omlx writes per-model settings (the MTP drafter block) here.
OMLX_BASE_PATH="${OMLX_BASE_PATH:-$HOME/.omlx}"
MODEL_SETTINGS_FILE="$OMLX_BASE_PATH/model_settings.json"
# The matched MTP drafter (bf16; 6-bit crashes on a quant-embedding reshape — item 29).
DRAFTER_DIR="${OMLX_DRAFTER_DIR:-$(cd "$(dirname "$0")/.." && pwd)/scratchpad/omlx-drafters/gemma-4-E4B-it-qat-assistant-bf16}"

PID_FILE="$RUN_DIR/omlx-server.pid"
LOG_FILE="$RUN_DIR/omlx-server.log"
PROXY_PID_FILE="$RUN_DIR/omlx-proxy.pid"
PROXY_LOG_FILE="$RUN_DIR/omlx-proxy.log"
PROXY_SCRIPT="$(cd "$(dirname "$0")" && pwd)/omlx_repair_proxy.py"
# The CLI is launched as `omlx serve` but the running process renames itself to
# `omlx-server`, so down/status must match THAT (matching "omlx serve" misses it
# and leaks a server that keeps :8088 bound).
SERVER_TAG="omlx-server"   # pattern used for down/status

# Poisoned HF endpoint (blackhole) for the structural offline guarantee.
HF_BLACKHOLE="${OMLX_HF_ENDPOINT:-http://127.0.0.1:1}"

if ! command -v omlx >/dev/null 2>&1; then
  echo "error: omlx not found on PATH — install with 'brew install omlx'." >&2
  exit 1
fi

# Resolve a Python that is NOT the broken pyenv 3.11.6 (libintl.8.dylib wedge);
# the proxy is pure stdlib so any 3.9+ works. Prefer an explicit override, then
# the repo venv, then python3.12, then bare python3.
_resolve_python() {
  if [ -n "${OMLX_PYTHON:-}" ]; then echo "$OMLX_PYTHON"; return 0; fi
  local venv="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/python3"
  if [ -x "$venv" ]; then echo "$venv"; return 0; fi
  if command -v python3.12 >/dev/null 2>&1; then echo "python3.12"; return 0; fi
  echo "python3"
}
PYTHON="$(_resolve_python)"

_weights_ok() {
  [ -f "$1/config.json" ] && ls "$1"/*.safetensors >/dev/null 2>&1
}

port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

# "Running" = the front port opencode connects to is up (proxy if enabled, else
# the omlx server itself).
is_running() {
  port_listening "$PORT"
}

# Ensure the dedicated single-model dir symlinks the pinned mlx weights, so omlx
# serves the byte-identical safetensors mlx.sh serves (no copy, no re-quant).
_ensure_model_symlink() {
  mkdir -p "$MODELS_DIR"
  if [ ! -e "$TARGET" ]; then
    if _weights_ok "$MLX_TARGET"; then
      ln -s "$MLX_TARGET" "$TARGET"
    fi
  fi
}

resolve_local_path() {
  _ensure_model_symlink
  if _weights_ok "$TARGET"; then echo "$MODELS_DIR"; else return 2; fi
}

# Write ~/.omlx/model_settings.json enabling the VLM-MTP drafter (item 29 adopt
# config: +37% decode vs mlx-lm, lossless, tool-call parity). No-op if the
# drafter isn't pulled or OMLX_MTP=0 (plain omlx then, still functional).
_write_model_settings() {
  if [ "$MTP" = "0" ]; then return 0; fi
  if [ ! -d "$DRAFTER_DIR" ]; then
    echo "note: MTP drafter not found at $DRAFTER_DIR — serving without speculative decoding." >&2
    echo "      (Pull mlx-community/gemma-4-E4B-it-qat-assistant-bf16 or set OMLX_MTP=0.)" >&2
    return 0
  fi
  mkdir -p "$OMLX_BASE_PATH"
  OMLX_MS_MODEL="$MODEL" OMLX_MS_DRAFTER="$DRAFTER_DIR" OMLX_MS_BS="$MTP_BLOCK_SIZE" \
    OMLX_MS_FILE="$MODEL_SETTINGS_FILE" "$PYTHON" - <<'PY'
import json, os
path = os.environ["OMLX_MS_FILE"]
model = os.environ["OMLX_MS_MODEL"]
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
data.setdefault("version", 1)
models = data.setdefault("models", {})
ms = models.setdefault(model, {})
ms["vlm_mtp_enabled"] = True
ms["vlm_mtp_draft_model"] = os.environ["OMLX_MS_DRAFTER"]
ms["vlm_mtp_draft_block_size"] = int(os.environ["OMLX_MS_BS"])
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, path)
print(f"[omlx] wrote MTP settings for {model} (block_size={ms['vlm_mtp_draft_block_size']})")
PY
}

_cache_flags() {
  if [ "$CACHE" = "0" ]; then
    printf '%s' "--no-cache"
    return 0
  fi
  # --paged-ssd-cache-dir is the master switch that turns the prefix cache ON.
  mkdir -p "$SSD_CACHE_DIR" 2>/dev/null || true
  local flags="--paged-ssd-cache-dir $SSD_CACHE_DIR"
  [ "$HOT_CACHE_MAX" != "auto" ] && [ -n "$HOT_CACHE_MAX" ] && \
    flags="$flags --hot-cache-max-size $HOT_CACHE_MAX"
  [ -n "$SSD_CACHE_MAX" ] && flags="$flags --paged-ssd-cache-max-size $SSD_CACHE_MAX"
  [ -n "$INITIAL_CACHE_BLOCKS" ] && flags="$flags --initial-cache-blocks $INITIAL_CACHE_BLOCKS"
  printf '%s' "$flags"
}

_start_server() {
  local path="$1"
  echo "Starting omlx ($MODEL) on 127.0.0.1:$SERVE_PORT (offline; logs: $LOG_FILE)…"
  _write_model_settings
  # shellcheck disable=SC2046  # intentional word-split of the cache flag string
  HF_HUB_OFFLINE=1 HTTP_PROXY="" HTTPS_PROXY="" http_proxy="" https_proxy="" \
    nohup omlx serve \
      --model-dir "$path" --host 127.0.0.1 --port "$SERVE_PORT" \
      --memory-guard "$MEMORY_GUARD" \
      --hf-endpoint "$HF_BLACKHOLE" --no-hf-cache \
      $(_cache_flags) \
      ${OMLX_SERVER_EXTRA_ARGS:-} \
      >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  local i
  for i in $(seq 1 90); do
    port_listening "$SERVE_PORT" && return 0
    grep -qiE "error|traceback|address already in use" "$LOG_FILE" 2>/dev/null && break
    sleep 1
  done
  echo "error: omlx did not come up — see $LOG_FILE" >&2
  tail -12 "$LOG_FILE" >&2 2>/dev/null || true
  return 1
}

_start_proxy() {
  echo "Starting omlx repair proxy on 127.0.0.1:$PORT -> :$SERVE_PORT (logs: $PROXY_LOG_FILE)…"
  OMLX_PROXY_LISTEN_HOST=127.0.0.1 OMLX_PROXY_PORT="$PORT" \
    OMLX_PROXY_UPSTREAM="http://127.0.0.1:$SERVE_PORT" \
    OMLX_PROXY_REPAIR="${OMLX_PROXY_REPAIR:-1}" \
    OMLX_PROXY_NO_THINK="${OMLX_PROXY_NO_THINK:-0}" \
    OMLX_PROXY_CAPTURE="${OMLX_PROXY_CAPTURE:-}" \
    nohup "$PYTHON" "$PROXY_SCRIPT" >"$PROXY_LOG_FILE" 2>&1 &
  echo $! >"$PROXY_PID_FILE"
  local i
  for i in $(seq 1 30); do
    port_listening "$PORT" && return 0
    grep -qiE "error|traceback|address already in use" "$PROXY_LOG_FILE" 2>/dev/null && break
    sleep 1
  done
  echo "error: proxy did not come up — see $PROXY_LOG_FILE" >&2
  tail -8 "$PROXY_LOG_FILE" >&2 2>/dev/null || true
  return 1
}

cmd_up() {
  if is_running; then
    echo "Already running on 127.0.0.1:$PORT (scripts/omlx.sh status)."
    return 0
  fi
  local path
  path="$(resolve_local_path)" || {
    echo "error: weights for $MODEL not present at $MLX_TARGET." >&2
    echo "Run 'make mlx-pull' first (omlx serves the same pinned weights)." >&2
    return 2
  }
  mkdir -p "$RUN_DIR"
  _start_server "$path" || return 1
  if [ "$OMLX_PROXY" != "0" ]; then
    _start_proxy || { cmd_down; return 1; }
    echo "⚠ omlx tool-call repair proxy ACTIVE (eos-strip + text-tool-calls; item 30)."
    echo "  Disable with OMLX_PROXY=0 (regresses tool-call success)."
  fi
  echo "Up on http://127.0.0.1:$PORT/v1. Stop with 'make omlx-down'."
}

cmd_down() {
  local stopped=1
  if [ -f "$PROXY_PID_FILE" ]; then
    kill "$(cat "$PROXY_PID_FILE")" 2>/dev/null && stopped=0
    rm -f "$PROXY_PID_FILE"
  fi
  pkill -f "omlx_repair_proxy.py" 2>/dev/null && stopped=0
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null && stopped=0
    rm -f "$PID_FILE"
  fi
  # Belt and suspenders: kill the omlx server by pattern (renamed to omlx-server).
  pkill -f "$SERVER_TAG" 2>/dev/null && stopped=0
  pkill -f "omlx serve" 2>/dev/null && stopped=0
  if [ "$stopped" -eq 0 ]; then echo "Stopped."; else echo "Not running."; fi
}

cmd_status() {
  if is_running; then
    echo "running  front=$PORT  model=$MODEL  upstream=$SERVE_PORT"
    if [ "$OMLX_PROXY" != "0" ]; then
      echo "proxy    on (repair=${OMLX_PROXY_REPAIR:-1} no_think=${OMLX_PROXY_NO_THINK:-0})"
      [ -f "$PROXY_PID_FILE" ] && echo "  proxy-pid=$(cat "$PROXY_PID_FILE")  log=$PROXY_LOG_FILE"
    else
      echo "proxy    off (OMLX_PROXY=0; opencode -> omlx directly)"
    fi
    [ -f "$PID_FILE" ] && echo "  server-pid=$(cat "$PID_FILE")  log=$LOG_FILE"
    echo "cache    $([ "$CACHE" = 0 ] && echo 'off (--no-cache)' || echo "on (hot-max=$HOT_CACHE_MAX)")   mtp=$MTP(bs=$MTP_BLOCK_SIZE)  guard=$MEMORY_GUARD"
  else
    echo "stopped  (model=$MODEL front=$PORT proxy=$([ "$OMLX_PROXY" != 0 ] && echo on || echo off))"
  fi
}

cmd_serve() {
  local path
  path="$(resolve_local_path)" || {
    echo "error: weights for $MODEL not present at $MLX_TARGET — run 'make mlx-pull'." >&2
    return 2
  }
  _write_model_settings
  echo "Serving $MODEL on http://127.0.0.1:$PORT/v1 (offline, no repair proxy; Ctrl-C to stop)"
  # shellcheck disable=SC2046
  exec env HF_HUB_OFFLINE=1 HTTP_PROXY="" HTTPS_PROXY="" \
    omlx serve --model-dir "$path" --host 127.0.0.1 --port "$PORT" \
      --memory-guard "$MEMORY_GUARD" --hf-endpoint "$HF_BLACKHOLE" --no-hf-cache \
      $(_cache_flags) ${OMLX_SERVER_EXTRA_ARGS:-}
}

cmd_pull() {
  # omlx serves the same pinned weights mlx.sh pulls; just verify presence and
  # wire the symlink. The actual download is 'make mlx-pull'.
  if _weights_ok "$MLX_TARGET"; then
    _ensure_model_symlink
    echo "ok: $MODEL present at $MLX_TARGET (symlinked into $MODELS_DIR)."
  else
    echo "error: $MODEL not present at $MLX_TARGET — run 'make mlx-pull' first." >&2
    return 2
  fi
}

# Health-gate (item 31): block until the front port opencode talks to is fully
# ready — (1) /v1/models returns a model AND (2) a tiny warmup completion through
# the proxy succeeds. The warmup is what makes this stronger than a bare
# /v1/models poll: it exercises the whole proxy->omlx path (incl. the upstream-
# readiness retry) and forces the model's first decode, so the first real
# opencode call can't race a not-yet-serving upstream (the item-30 server-reload
# race). Timeout via OMLX_HEALTH_TIMEOUT (default 180s).
_health_gate() {
  OMLX_HG_URL="http://127.0.0.1:$PORT/v1" \
  OMLX_HG_TIMEOUT="${OMLX_HEALTH_TIMEOUT:-180}" "$PYTHON" - <<'PY'
import json, os, sys, time, urllib.error, urllib.request
base = os.environ["OMLX_HG_URL"].rstrip("/")
deadline = time.monotonic() + float(os.environ["OMLX_HG_TIMEOUT"])


def _models():
    try:
        with urllib.request.urlopen(base + "/models", timeout=8) as r:
            return json.loads(r.read().decode()).get("data") or []
    except (urllib.error.URLError, OSError, ValueError):
        return []


data = []
while time.monotonic() < deadline:
    data = _models()
    if data:
        break
    time.sleep(2)
if not data:
    print("health: /v1/models never returned a model", file=sys.stderr)
    sys.exit(1)
model = data[0].get("id", "")

body = json.dumps({
    "model": model, "messages": [{"role": "user", "content": "ok"}],
    "max_tokens": 8, "temperature": 0, "stream": False,
}).encode()
t0 = time.monotonic()
while True:
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            obj = json.loads(r.read().decode())
        if obj.get("choices"):
            print(f"health: OK model={model} warmup={time.monotonic() - t0:.1f}s")
            sys.exit(0)
        print("health: warmup completion returned no choices", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        if time.monotonic() < deadline:
            time.sleep(2)
            continue
        print(f"health: warmup completion failed: {e}", file=sys.stderr)
        sys.exit(1)
PY
}

cmd_health() {
  if ! is_running; then
    echo "health: not running (front :$PORT is down — run 'scripts/omlx.sh up')" >&2
    return 1
  fi
  _health_gate
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  health) cmd_health ;;
  serve) cmd_serve ;;
  pull) cmd_pull ;;
  *)
    echo "usage: scripts/omlx.sh up|down|status|health|serve|pull" >&2
    exit 2
    ;;
esac
