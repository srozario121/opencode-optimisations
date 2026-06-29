#!/bin/bash
# Local-model coding-agent server controller (TODO item 8 / docs/opencode-local.md).
#
# Serves a local Gemma 4 QAT model through mlx-lm's OpenAI-compatible HTTP
# server, bound to 127.0.0.1, for opencode (or any OpenAI-compatible client).
# No code or prompt leaves the machine: the server is a local subprocess and,
# once weights are pulled, runs with HF_HUB_OFFLINE=1 (no model-hub egress).
#
# mlx-lm is run **isolated** via `uvx` rather than as a project dependency: it
# requires transformers>=5, which conflicts with the inbox privacy stack's
# pinned transformers<5. uvx gives it its own environment (see pyproject.toml).
#
# Usage:
#   scripts/mlx.sh pull     # one-time weight download (online)
#   scripts/mlx.sh up       # start server (+ repair proxy) in the background
#   scripts/mlx.sh down     # stop the background server (and proxy)
#   scripts/mlx.sh status   # is it running? on what port / model?
#   scripts/mlx.sh serve    # run the raw server in the foreground (no proxy)
#   scripts/mlx.sh opencode-config   # install the opencode provider config
#   scripts/mlx.sh jaeger-up    # start only Jaeger (tracing) + write OTLP env
#   scripts/mlx.sh jaeger-down  # stop only the Jaeger this script started
#
# By default 'up' runs a small tool-call REPAIR proxy in front of the model
# (workaround for mlx-lm Gemma 4 parser bugs #1096/#1125 — see
# scripts/mlx_repair_proxy.py). opencode always talks to MLX_PORT; the proxy
# forwards to the server on MLX_UPSTREAM_PORT. This is temporary: remove it once
# PR #1142 merges and MLX_LM_VERSION is bumped.
#
# Overrides (env): MLX_MODEL, MLX_REVISION, MLX_PORT — e.g. to try another size
# on a larger machine: MLX_MODEL=mlx-community/gemma-4-12B-it-qat-4bit. An
# override without a matching MLX_REVISION uses the `main` revision.
# Small model (opencode small_model / session-title slot — TODO item 12): a tiny
# Gemma 3 270M QAT model served on a SECOND port (MLX_SMALL_PORT, default 8082)
# so title-gen no longer contends with the main model for the single Metal GPU.
# 'pull' fetches it too; 'up' serves it; opencode-config wires small_model.
# Disable with MLX_SMALL=0. Override with MLX_SMALL_MODEL / MLX_SMALL_REVISION /
# MLX_SMALL_PORT. See docs/small-model-research.md + docs/opencode-local.md.
# Proxy: MLX_PROXY=0 disables it (opencode -> server directly); MLX_PROXY_REPAIR=0
# keeps it in path but passes through unmodified; MLX_UPSTREAM_PORT sets the
# server's port when the proxy is on (default 8081).
# Tracing: 'up' starts a local Jaeger (if a jaeger binary is on PATH) and writes
# OTLP env vars to $RUN_DIR/opencode-otel.env; `source` that before launching
# opencode to ship session/llm/tool spans to Jaeger's UI (http://127.0.0.1:16686).
# Disable with MLX_OTEL=0. Override OTLP with MLX_OTEL_PROTOCOL=grpc, MLX_OTEL_*_PORT.
# 'up'/'jaeger-up'/'opencode-config' also idempotently patch the opencode otel
# plugin so trace spans flush on end (SimpleSpanProcessor) — without it the plugin
# batches spans and never flushes in opencode's server teardown, so Jaeger stays
# empty (see _vendor_otel_plugin). Disable that patch alone with MLX_OTEL_PATCH=0.
# Harness (TODO item 13): opencode-config also emits a separate opencode-native
# coding-skill set (skill bodies load only on the `skill` tool, so they are NOT
# resident in the prefill prefix) via skills.paths, and writes read-cap defaults
# (mlx-read-cap.env) for the hard read cap whose LOGIC lives in the tracked
# .opencode/tools/read.ts. Disable per-surface with MLX_SKILLS=0 / MLX_READ_CAP=0;
# tune with MLX_READ_MAX_LINES (1500) / MLX_READ_MAX_COLUMNS (200). Generation is
# idempotent (re-run -> byte-identical). See docs/opencode-local.md (item 13).
set -uo pipefail

# --- Pins (single source of truth; mirrored in docs/opencode-local.md) --------
# Gemma 4 QAT (quantization-aware training) 4-bit. E4B (effective 4B, ~6.3 GB)
# is the size that fits this 16 GB M1 with safe headroom; 12B QAT (~10 GB) needs
# ≥24 GB; E2B (~4 GB) is the lighter option. See docs/opencode-local.md.
MLX_LM_VERSION="0.31.3"
DEFAULT_MODEL="mlx-community/gemma-4-E4B-it-qat-4bit"
DEFAULT_REVISION="0f35c6f6d386f7f74e628bd7c6526ce531212300"
DEFAULT_PORT="8080"

MODEL="${MLX_MODEL:-$DEFAULT_MODEL}"
if [ "$MODEL" = "$DEFAULT_MODEL" ]; then
  REVISION="${MLX_REVISION:-$DEFAULT_REVISION}"
else
  REVISION="${MLX_REVISION:-main}"
fi
PORT="${MLX_PORT:-$DEFAULT_PORT}"

# Tool-call repair proxy (workaround for mlx-lm Gemma 4 parser bugs #1096/#1125;
# see scripts/mlx_repair_proxy.py and docs/opencode-local.md). When enabled
# (default), the proxy listens on PORT (what opencode talks to) and mlx_lm.server
# moves to UPSTREAM_PORT — so toggling the proxy never changes the opencode
# config. Disable with MLX_PROXY=0 (opencode then talks straight to the server).
# Remove the proxy entirely once PR #1142 merges and MLX_LM_VERSION is bumped:
#   https://github.com/ml-explore/mlx-lm/pull/1142
MLX_PROXY="${MLX_PROXY:-1}"
UPSTREAM_PORT="${MLX_UPSTREAM_PORT:-8081}"
if [ "$MLX_PROXY" != "0" ]; then SERVE_PORT="$UPSTREAM_PORT"; else SERVE_PORT="$PORT"; fi

# uvx resolves mlx-lm against PyPI on EVERY invocation by default — so even with
# HF_HUB_OFFLINE=1 (which only blocks model-hub egress), an offline `up`/`serve`
# dies on a PyPI DNS lookup before the server ever starts. Serve from uv's cache
# only (`uvx --offline`); `pull` warms that cache while online, so the offline
# serving guarantee in docs/opencode-local.md actually holds. Override with
# MLX_UVX_OFFLINE=0 if you need uvx to reach PyPI (e.g. warming a freshly-bumped
# MLX_LM_VERSION you didn't `make mlx-pull` for).
UVX_OFFLINE="${MLX_UVX_OFFLINE:-1}"
if [ "$UVX_OFFLINE" != "0" ]; then UVX_OFFLINE_FLAG="--offline"; else UVX_OFFLINE_FLAG=""; fi

RUN_DIR="${MLX_RUN_DIR:-$HOME/.config/opencode-optimisations}"
PID_FILE="$RUN_DIR/mlx-server.pid"
LOG_FILE="$RUN_DIR/mlx-server.log"
PROXY_PID_FILE="$RUN_DIR/mlx-proxy.pid"
PROXY_LOG_FILE="$RUN_DIR/mlx-proxy.log"
PROXY_SCRIPT="$(cd "$(dirname "$0")" && pwd)/mlx_repair_proxy.py"
SERVER_TAG="mlx_lm.server"   # pattern used for down/status

# Patched LOCAL copy of the opencode otel plugin (see _vendor_otel_plugin). opencode
# loads it by absolute path, so an @latest re-fetch of the npm cache can't revert
# the patches. The patcher lives in scripts/patch_otel_plugin.py.
OTEL_PLUGIN_DIR="$RUN_DIR/opencode-plugin-otel"
OTEL_PLUGIN_FILE="$OTEL_PLUGIN_DIR/index.js"
OTEL_PATCH_SCRIPT="$(cd "$(dirname "$0")" && pwd)/patch_otel_plugin.py"

# --- OpenTelemetry tracing (opencode -> Jaeger) -------------------------------
# opencode emits no telemetry natively; the @devtheops/opencode-plugin-otel
# plugin (declared in opencode.json by `opencode-config`) exports session/llm/
# tool SPANS over OTLP. Jaeger ingests them on 4318 (HTTP) / 4317 (gRPC) and
# serves its UI on 16686 — all on 127.0.0.1, no egress (docs/opencode-local.md).
#
# mlx.sh runs the *model server*, not opencode, so it cannot inject env into
# opencode's process. Instead `up` (a) starts a local Jaeger if one is on PATH
# and (b) writes the OTLP env vars opencode's plugin reads to an env file you
# SOURCE before launching opencode. Disable the whole block with MLX_OTEL=0.
MLX_OTEL="${MLX_OTEL:-1}"
OTEL_HTTP_PORT="${MLX_OTEL_HTTP_PORT:-4318}"
OTEL_GRPC_PORT="${MLX_OTEL_GRPC_PORT:-4317}"
OTEL_UI_PORT="${MLX_OTEL_UI_PORT:-16686}"
OTEL_PROTOCOL="${MLX_OTEL_PROTOCOL:-http/protobuf}"   # or: grpc
if [ "$OTEL_PROTOCOL" = "grpc" ]; then
  OTEL_ENDPOINT="${MLX_OTEL_ENDPOINT:-http://127.0.0.1:$OTEL_GRPC_PORT}"
else
  OTEL_ENDPOINT="${MLX_OTEL_ENDPOINT:-http://127.0.0.1:$OTEL_HTTP_PORT}"
fi
JAEGER_PID_FILE="$RUN_DIR/jaeger.pid"
JAEGER_LOG_FILE="$RUN_DIR/jaeger.log"
OTEL_ENV_FILE="$RUN_DIR/opencode-otel.env"

# Weights live in a plain local directory (not the HF cache): mlx_lm.server
# loads them via --model <dir>, and a curl-with-resume pull is robust on flaky
# connections (HF's downloader restarts files on etag changes mid-download).
# Fully local by construction — no hub involved once pulled.
MODELS_DIR="${MLX_MODELS_DIR:-$HOME/.config/opencode-optimisations/mlx-models}"
TARGET="$MODELS_DIR/${MODEL##*/}"

# --- Small (title / small_model) model — TODO item 12, task A (path 2) --------
# A tiny Gemma 3 270M QAT *instruct* model served on a SECOND 127.0.0.1 port for
# opencode's `small_model` slot (session-title generation). It co-resides with
# the main E4B model (~6.3 GB) at ~0.25 GB resident, so the title call no longer
# steals the main model's single-Metal-GPU time during session start (the cause
# of the ~157s startup stall — see docs/opencode-local.md + the research doc
# docs/small-model-research.md). Disable with MLX_SMALL=0 (opencode then falls
# back to the main model for titles, reinstating the contention). The QAT 4-bit
# pick + its confirmed revision are from docs/small-model-research.md.
MLX_SMALL="${MLX_SMALL:-1}"
SMALL_DEFAULT_MODEL="mlx-community/gemma-3-270m-it-qat-4bit"
SMALL_DEFAULT_REVISION="71fb198f2649a80259f9f5fe878dd9dd25638a65"
SMALL_MODEL="${MLX_SMALL_MODEL:-$SMALL_DEFAULT_MODEL}"
if [ "$SMALL_MODEL" = "$SMALL_DEFAULT_MODEL" ]; then
  SMALL_REVISION="${MLX_SMALL_REVISION:-$SMALL_DEFAULT_REVISION}"
else
  SMALL_REVISION="${MLX_SMALL_REVISION:-main}"
fi
SMALL_PORT="${MLX_SMALL_PORT:-8082}"                    # front port opencode small_model talks to
SMALL_UPSTREAM_PORT="${MLX_SMALL_UPSTREAM_PORT:-8083}"  # the small mlx_lm.server
# Mirror the main model's proxy port-shuffle so the opencode small_model config
# is stable whether or not the tracing proxy is in path (see MLX_PROXY above).
if [ "$MLX_PROXY" != "0" ]; then SMALL_SERVE_PORT="$SMALL_UPSTREAM_PORT"; else SMALL_SERVE_PORT="$SMALL_PORT"; fi
SMALL_TARGET="$MODELS_DIR/${SMALL_MODEL##*/}"
SMALL_PID_FILE="$RUN_DIR/mlx-small.pid"
SMALL_LOG_FILE="$RUN_DIR/mlx-small.log"

# TODO item 13, thread 1 — emit a separate opencode-native coding-skill set
# (read-range discipline, edit patterns) via opencode's native Skill subsystem.
# Skill bodies load ONLY when the model invokes the `skill` tool, so they never
# sit in the always-resident prefill prefix (unlike `instructions`). Disable the
# whole skills surface (and drop the skills.paths config entry) with MLX_SKILLS=0.
MLX_SKILLS="${MLX_SKILLS:-1}"
# TODO item 13, thread 3 — the hard read-cap DEFAULTS the generator writes into
# opencode's launch env (the cap LOGIC lives in the tracked .opencode/tools/read.ts).
# READ_MAX_LINES × READ_MAX_COLUMNS bounds worst-case per-turn prompt size under
# the ~40–50K Metal-OOM ceiling. Set MLX_READ_CAP=0 to omit the env block (the
# tool then uses its own built-in defaults).
MLX_READ_CAP="${MLX_READ_CAP:-1}"
MLX_READ_MAX_LINES="${MLX_READ_MAX_LINES:-1500}"
MLX_READ_MAX_COLUMNS="${MLX_READ_MAX_COLUMNS:-200}"
SMALL_PROXY_PID_FILE="$RUN_DIR/mlx-small-proxy.pid"
SMALL_PROXY_LOG_FILE="$RUN_DIR/mlx-small-proxy.log"

if ! command -v uvx >/dev/null 2>&1; then
  echo "error: uvx (uv) not found on PATH — install uv first." >&2
  exit 2
fi

# True if a model directory has its config + weights present.
_weights_ok() {
  [ -f "$1/config.json" ] && ls "$1"/*.safetensors >/dev/null 2>&1
}

# Echo the local model dir if the weights are present, else fail (this is the
# "never download at serve time" pre-flight).
resolve_local_path() {
  if _weights_ok "$TARGET"; then echo "$TARGET"; else return 2; fi
}

port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

# "Running" = the front port opencode connects to is up (proxy if enabled,
# else the server itself).
is_running() {
  port_listening "$PORT"
}

# True if Jaeger's OTLP receiver is already listening (HTTP or gRPC).
jaeger_listening() {
  port_listening "$OTEL_HTTP_PORT" || port_listening "$OTEL_GRPC_PORT"
}

# Locate a Jaeger all-in-one binary. v2 is `jaeger` (OTLP on by default); v1 is
# `jaeger-all-in-one` (needs --collector.otlp.enabled). Echo "<bin>|<v1|v2>".
_jaeger_bin() {
  if command -v jaeger >/dev/null 2>&1; then echo "jaeger|v2"; return 0; fi
  if command -v jaeger-all-in-one >/dev/null 2>&1; then echo "jaeger-all-in-one|v1"; return 0; fi
  return 1
}

# Write the OTLP env vars opencode's otel plugin reads. mlx.sh can't inject env
# into opencode's process, so you source this before launching opencode.
_write_otel_env() {
  cat >"$OTEL_ENV_FILE" <<EOF
# opencode OpenTelemetry -> Jaeger — written by scripts/mlx.sh up. Source me:
#   source $OTEL_ENV_FILE
export OPENCODE_ENABLE_TELEMETRY=1
export OPENCODE_OTLP_ENDPOINT=$OTEL_ENDPOINT
export OPENCODE_OTLP_PROTOCOL=$OTEL_PROTOCOL
EOF
}

# Start a local Jaeger all-in-one if one isn't already listening. Non-fatal:
# MLX serving must succeed even when Jaeger isn't installed, so this only warns.
_start_jaeger() {
  if jaeger_listening; then
    echo "Jaeger already up (OTLP :$OTEL_HTTP_PORT/:$OTEL_GRPC_PORT, UI http://127.0.0.1:$OTEL_UI_PORT)."
    return 0
  fi
  local found bin ver
  found="$(_jaeger_bin)" || {
    echo "⚠ tracing on (MLX_OTEL=1) but no Jaeger binary on PATH — opencode is" >&2
    echo "  configured and will export once a Jaeger is up. Install the" >&2
    echo "  darwin-arm64 all-in-one binary (no Docker needed):" >&2
    echo "    https://github.com/jaegertracing/jaeger/releases/latest" >&2
    echo "  …or: docker run -d -e COLLECTOR_OTLP_ENABLED=true \\" >&2
    echo "       -p $OTEL_UI_PORT:16686 -p $OTEL_GRPC_PORT:4317 -p $OTEL_HTTP_PORT:4318 jaegertracing/all-in-one" >&2
    echo "  (silence with MLX_OTEL=0.)" >&2
    return 0
  }
  bin="${found%%|*}"; ver="${found##*|}"
  echo "Starting Jaeger ($bin) — OTLP :$OTEL_HTTP_PORT/:$OTEL_GRPC_PORT, UI http://127.0.0.1:$OTEL_UI_PORT (logs: $JAEGER_LOG_FILE)…"
  if [ "$ver" = "v1" ]; then
    nohup "$bin" --collector.otlp.enabled=true \
      --collector.otlp.http.host-port=127.0.0.1:"$OTEL_HTTP_PORT" \
      --collector.otlp.grpc.host-port=127.0.0.1:"$OTEL_GRPC_PORT" \
      >"$JAEGER_LOG_FILE" 2>&1 &
  else
    # v2 enables OTLP on 4317/4318 and the UI on 16686 by default.
    nohup "$bin" >"$JAEGER_LOG_FILE" 2>&1 &
  fi
  echo $! >"$JAEGER_PID_FILE"
  for _ in $(seq 1 30); do
    jaeger_listening && return 0
    grep -qiE "error|fatal|address already in use" "$JAEGER_LOG_FILE" 2>/dev/null && break
    sleep 1
  done
  echo "⚠ Jaeger did not come up — see $JAEGER_LOG_FILE (MLX itself is unaffected)." >&2
  tail -6 "$JAEGER_LOG_FILE" >&2 2>/dev/null
  return 0   # non-fatal
}

# Stop the Jaeger this script started (tracked by PID file). Returns 0 if it
# killed a process, 1 if there was nothing to stop. A Jaeger you launched
# yourself has no PID file here, so it's left running. Shared by `down` and
# `jaeger-down`. Note: all-in-one keeps traces in memory, so stopping it
# discards collected spans.
_stop_jaeger() {
  [ -f "$JAEGER_PID_FILE" ] || return 1
  kill "$(cat "$JAEGER_PID_FILE")" 2>/dev/null
  local rc=$?
  rm -f "$JAEGER_PID_FILE"
  return $rc
}

# TEMPORARY workaround — vendor a PATCHED local copy of the opencode OpenTelemetry
# plugin (@devtheops/opencode-plugin-otel) and point opencode at it by absolute
# path, so an @latest re-fetch of the npm cache can't revert the patches (the
# reason this replaced the old in-place cache edit). patch_otel_plugin.py applies
# two fixes (full rationale there + in docs/opencode-local.md):
#   1. flush — BatchSpanProcessor -> SimpleSpanProcessor, so spans export the
#      instant they end (opencode tears the plugin down without firing the batch
#      flush hooks, so otherwise Jaeger stays "connected but empty").
#   2. per-session trace grouping — seed every span with a deterministic trace id
#      = sha256(sessionID)[:32] (the SAME derivation the repair proxy uses), so a
#      session's otherwise-scattered spans + the proxy's system-prompt spans
#      collapse into one Jaeger trace per session.
# Disable both with MLX_OTEL_PATCH=0. Remove once upstream flushes on shutdown and
# exposes per-session context (https://github.com/DEVtheOPS/opencode-plugin-otel).
_vendor_otel_plugin() {
  [ "${MLX_OTEL_PATCH:-1}" != "0" ] || return 0
  local src=""
  for f in "$HOME"/.cache/opencode/packages/@devtheops/opencode-plugin-otel@*/node_modules/@devtheops/opencode-plugin-otel/dist/index.js; do
    [ -f "$f" ] && { src="$f"; break; }
  done
  if [ -z "$src" ]; then
    echo "note: opencode otel plugin not in cache yet — launch opencode once (it" >&2
    echo "  fetches the plugin), then re-run 'make mlx-jaeger-up' to vendor +" >&2
    echo "  patch the local copy and repoint opencode at it." >&2
    return 0
  fi
  mkdir -p "$OTEL_PLUGIN_DIR"
  if python3 "$OTEL_PATCH_SCRIPT" "$src" "$OTEL_PLUGIN_FILE"; then
    _repoint_otel_plugin
  else
    echo "⚠ could not patch the otel plugin (see above) — tracing may be degraded." >&2
  fi
}

# Point opencode.json's plugin array at the vendored copy, dropping the npm entry
# so the patched copy is the ONLY one loaded (both loading = duplicate spans, one
# set in random traces). No-op until the local copy exists.
_repoint_otel_plugin() {
  [ -f "$OTEL_PLUGIN_FILE" ] || return 0
  local cfg="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}/opencode.json"
  [ -f "$cfg" ] || return 0
  OTEL_PLUGIN_FILE="$OTEL_PLUGIN_FILE" OPENCODE_CFG="$cfg" python3 - <<'PY'
import json, os
cfg, vend = os.environ["OPENCODE_CFG"], os.environ["OTEL_PLUGIN_FILE"]
npm = "@devtheops/opencode-plugin-otel"
try:
    with open(cfg) as f:
        data = json.load(f)
except Exception:
    raise SystemExit(0)
pl = data.get("plugin")
if isinstance(pl, list):
    new = [p for p in pl if p not in (npm, vend)]
    new.append(vend)
    if new != pl:
        data["plugin"] = new
        with open(cfg, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"Repointed opencode plugin -> {vend}")
PY
}

# curl-with-resume one file until it reaches its known size. Short --speed-time
# forces a reconnect when the connection hangs (the failure mode on this link),
# and -C - resumes from the bytes already on disk.
_fetch_file() {
  local url="$1" out="$2" size="$3" path="$4"
  mkdir -p "$(dirname "$out")"
  local have; have=$(stat -f%z "$out" 2>/dev/null || echo 0)
  if [ "$size" != "0" ] && [ "$have" -ge "$size" ] 2>/dev/null; then echo "  ok   $path"; return 0; fi
  echo "  get  $path ($(( size / 1048576 )) MB)"
  local attempt=0
  while :; do
    have=$(stat -f%z "$out" 2>/dev/null || echo 0)
    if [ "$size" != "0" ] && [ "$have" -ge "$size" ] 2>/dev/null; then return 0; fi
    attempt=$((attempt + 1))
    if [ "$attempt" -gt "${MLX_PULL_ATTEMPTS:-80}" ]; then
      echo "error: gave up downloading $path" >&2; return 1
    fi
    curl -sL -C - -o "$out" \
      --connect-timeout 15 --max-time 120 --speed-time 20 --speed-limit 50000 "$url"
    [ "$size" = "0" ] && return 0   # unknown size: single attempt
  done
}

# Pull one model repo (model, revision, target dir) via the curl-resume + sha256
# path. Shared by the main and small (title-slot) models.
_pull_one() {
  local model="$1" revision="$2" target="$3"
  echo "Pulling $model @ ${revision:0:12} -> $target"
  mkdir -p "$target"
  # File list (path, size, lfs-oid) from the HF API at the pinned revision.
  local listing
  listing=$(curl -s "https://huggingface.co/api/models/$model/tree/$revision?recursive=1" \
    | python3 -c "
import sys, json
for f in json.load(sys.stdin):
    if f.get('type') == 'file':
        sz = (f.get('lfs') or {}).get('size') or f.get('size') or 0
        oid = (f.get('lfs') or {}).get('oid') or ''
        print('%s\t%s\t%s' % (f['path'], sz, oid))
")
  [ -z "$listing" ] && { echo 'error: could not list repo files (offline?)' >&2; return 1; }
  while IFS=$'\t' read -r path size oid; do
    [ -z "$path" ] && continue
    _fetch_file "https://huggingface.co/$model/resolve/$revision/$path" \
      "$target/$path" "$size" "$path" || return 1
    # Integrity: verify large LFS weights against their sha256 (the lfs oid).
    if [ -n "$oid" ] && [ "${path##*.}" = "safetensors" ]; then
      local actual; actual=$(shasum -a 256 "$target/$path" | awk '{print $1}')
      if [ "$actual" != "$oid" ]; then
        echo "error: sha256 mismatch for $path (corrupt) — re-run 'make mlx-pull'" >&2
        return 1
      fi
      echo "  ✓ sha256 $path"
    fi
  done <<< "$listing"
  echo "Downloaded $model -> $target"
}

cmd_pull() {
  _pull_one "$MODEL" "$REVISION" "$TARGET" || return 1
  # The tiny title-slot model (TODO item 12, task A path 2). Skip with MLX_SMALL=0.
  if [ "$MLX_SMALL" != "0" ]; then
    _pull_one "$SMALL_MODEL" "$SMALL_REVISION" "$SMALL_TARGET" || return 1
  fi

  # Warm uv's tool cache for mlx-lm while we're still online, so serving can run
  # fully offline (`uvx --offline`). Without this, the first 'make mlx-up' after
  # a fresh pull — or after bumping MLX_LM_VERSION — would have to reach PyPI,
  # defeating the offline guarantee.
  echo "Warming mlx-lm==$MLX_LM_VERSION tool environment (so serving is offline)…"
  if uvx --from "mlx-lm==$MLX_LM_VERSION" "$SERVER_TAG" --help >/dev/null 2>&1; then
    echo "  ✓ mlx-lm cached — serving will run offline"
  else
    echo "  ⚠ could not warm mlx-lm cache — first 'make mlx-up' may need network" >&2
  fi
}

# Start a detached mlx_lm.server. Args: label path serve_port pid_file log_file.
# Returns once the port is listening, or non-zero on failure.
_start_server() {
  local label="$1" path="$2" serve_port="$3" pid_file="$4" log_file="$5"
  echo "Starting $label on 127.0.0.1:$serve_port (offline; logs: $log_file)…"
  # Optional default chat-template kwargs forwarded to the tokenizer's
  # apply_chat_template (item 24: e.g. MLX_CHAT_TEMPLATE_ARGS='{"enable_thinking":false}'
  # to serve a thinking-capable model — Qwen3.5 — with thinking OFF by default, so the
  # default request path matches non-thinking baselines without the client having to send
  # chat_template_kwargs). Off by default => Gemma baseline serving is byte-unchanged.
  # Bash-3.2-safe empty-array expansion (macOS /bin/bash + set -u).
  local extra_args=()
  if [ -n "${MLX_CHAT_TEMPLATE_ARGS:-}" ]; then
    extra_args=(--chat-template-args "$MLX_CHAT_TEMPLATE_ARGS")
  fi
  # Optional raw mlx_lm.server flags appended verbatim (item 24.3 OOM fix: the 4B
  # K=3 run crashed when the prompt cache climbed unbounded to 4.6 GB / 10 seqs —
  # cap it with MLX_SERVER_EXTRA_ARGS='--prompt-cache-bytes <N> --prompt-concurrency 1'.
  # Word-split intentionally (multiple flags); off by default => baseline byte-unchanged.
  # restart_server() re-runs `mlx.sh up`, so an exported value survives OOM restarts.
  if [ -n "${MLX_SERVER_EXTRA_ARGS:-}" ]; then
    # shellcheck disable=SC2206  # intentional word-split of a flag string (bash)
    extra_args+=($MLX_SERVER_EXTRA_ARGS)
  fi
  # HF_HUB_OFFLINE=1 => no model-hub egress; uvx --offline => no PyPI egress.
  HF_HUB_OFFLINE=1 nohup uvx $UVX_OFFLINE_FLAG --from "mlx-lm==$MLX_LM_VERSION" "$SERVER_TAG" \
    --model "$path" --host 127.0.0.1 --port "$serve_port" \
    ${extra_args[@]+"${extra_args[@]}"} \
    >"$log_file" 2>&1 &
  echo $! >"$pid_file"
  for _ in $(seq 1 60); do
    port_listening "$serve_port" && return 0
    grep -qiE "error|traceback|address already in use" "$log_file" 2>/dev/null && break
    sleep 1
  done
  echo "error: server did not come up — see $log_file" >&2
  tail -8 "$log_file" >&2 2>/dev/null
  if grep -qiE "pypi\.org|dns error|failed to lookup|could not connect|offline" "$log_file" 2>/dev/null; then
    echo "hint: uvx couldn't reach/resolve mlx-lm from its cache. Run 'make mlx-pull'" >&2
    echo "      once while online to warm it, or retry with MLX_UVX_OFFLINE=0." >&2
  fi
  return 1
}

# Start a detached proxy. Args: listen_port upstream_port pid_file log_file
# repair role otel_service. `repair` toggles the #1125 tool-call repair (off for
# the small model — titles use no tools); `role` is stamped onto every span so
# the title/small-model call is identifiable in Jaeger (TODO item 12, task D).
_start_proxy() {
  local listen_port="$1" upstream_port="$2" pid_file="$3" log_file="$4"
  local repair="$5" role="$6" otel_service="$7"
  echo "Starting proxy [$role] on 127.0.0.1:$listen_port -> :$upstream_port (repair=$repair; logs: $log_file)…"
  # Tracing: when MLX_OTEL is on, the proxy emits a span per chat request
  # carrying the SYSTEM PROMPT + agent role (which opencode's otel plugin can't
  # see). Always target the OTLP/HTTP receiver (:$OTEL_HTTP_PORT) regardless of
  # OTEL_PROTOCOL.
  local proxy_otel=0
  [ "$MLX_OTEL" != "0" ] && proxy_otel=1
  # MLX_PROXY_CAPTURE (optional, off by default): dir to dump each chat
  # request+response for tool-call-reliability debugging (item 16 L6). Set it in
  # the environment before `make mlx-up` to enable on the next run.
  MLX_PROXY_LISTEN_HOST=127.0.0.1 MLX_PROXY_LISTEN_PORT="$listen_port" \
    MLX_PROXY_UPSTREAM="http://127.0.0.1:$upstream_port" \
    MLX_PROXY_REPAIR="$repair" \
    MLX_PROXY_ROLE="$role" \
    MLX_PROXY_CAPTURE="${MLX_PROXY_CAPTURE:-}" \
    MLX_PROXY_NO_THINK="${MLX_PROXY_NO_THINK:-0}" \
    MLX_PROXY_SEED="${MLX_PROXY_SEED:-}" \
    MLX_PROXY_OTEL="$proxy_otel" \
    MLX_PROXY_OTEL_ENDPOINT="http://127.0.0.1:$OTEL_HTTP_PORT" \
    MLX_PROXY_OTEL_SERVICE="$otel_service" \
    nohup python3 "$PROXY_SCRIPT" >"$log_file" 2>&1 &
  echo $! >"$pid_file"
  for _ in $(seq 1 30); do
    port_listening "$listen_port" && return 0
    grep -qiE "error|traceback|address already in use" "$log_file" 2>/dev/null && break
    sleep 1
  done
  echo "error: proxy [$role] did not come up — see $log_file" >&2
  tail -8 "$log_file" >&2 2>/dev/null
  return 1
}

cmd_up() {
  if is_running; then
    echo "Already running on 127.0.0.1:$PORT (scripts/mlx.sh status)."
    return 0
  fi
  local path
  path="$(resolve_local_path)" || {
    echo "error: weights for $MODEL are not present at $TARGET." >&2
    echo "Run 'make mlx-pull' first — the server never downloads at start time." >&2
    return 2
  }
  mkdir -p "$RUN_DIR"
  _start_server "$MODEL" "$path" "$SERVE_PORT" "$PID_FILE" "$LOG_FILE" || return 1
  if [ "$MLX_PROXY" != "0" ]; then
    _start_proxy "$PORT" "$SERVE_PORT" "$PROXY_PID_FILE" "$PROXY_LOG_FILE" \
      "${MLX_PROXY_REPAIR:-1}" "main" "mlx-proxy" || { cmd_down; return 1; }
    echo "⚠ tool-call repair proxy is ACTIVE (mlx-lm #1096/#1125 workaround)."
    echo "  Remove once PR #1142 merges & MLX_LM_VERSION is bumped: https://github.com/ml-explore/mlx-lm/pull/1142"
    echo "  Disable with MLX_PROXY=0."
  fi
  # Small (title-slot) model on its own port — TODO item 12, task A path 2.
  # Non-fatal: if it can't start, opencode just falls back to the main model for
  # titles (reinstating the GPU contention), so the main loop must not abort.
  if [ "$MLX_SMALL" != "0" ]; then
    if _weights_ok "$SMALL_TARGET"; then
      if _start_server "$SMALL_MODEL" "$SMALL_TARGET" "$SMALL_SERVE_PORT" \
           "$SMALL_PID_FILE" "$SMALL_LOG_FILE"; then
        echo "Small model (title slot) up on 127.0.0.1:$SMALL_PORT — opencode small_model."
        if [ "$MLX_PROXY" != "0" ]; then
          # repair OFF (titles use no tools); role=small_model so Jaeger tags the
          # title call (task D). A failure here only loses tracing, not titles.
          _start_proxy "$SMALL_PORT" "$SMALL_SERVE_PORT" "$SMALL_PROXY_PID_FILE" \
            "$SMALL_PROXY_LOG_FILE" "0" "small_model" "mlx-proxy-small" \
            || echo "⚠ small-model proxy did not start — titles still work, just untraced." >&2
        fi
      else
        echo "⚠ small model failed to start — titles fall back to the main model (see $SMALL_LOG_FILE)." >&2
      fi
    else
      echo "note: small model not pulled at $SMALL_TARGET — run 'make mlx-pull'." >&2
      echo "      Titles use the main model until then. (Disable the slot with MLX_SMALL=0.)" >&2
    fi
  fi
  if [ "$MLX_OTEL" != "0" ]; then
    _write_otel_env
    _start_jaeger
    _vendor_otel_plugin
    echo "Tracing: opencode -> Jaeger. Before launching opencode, run:"
    echo "    source $OTEL_ENV_FILE"
    echo "  then view spans at http://127.0.0.1:$OTEL_UI_PORT (service: opencode)."
  fi
  echo "Up on http://127.0.0.1:$PORT/v1. Stop with 'make mlx-down'."
}

cmd_down() {
  local stopped=1
  # Jaeger first, but only the one `up` started (see _stop_jaeger).
  _stop_jaeger && stopped=0
  # Small-model proxy + server (TODO item 12 path 2), then the main pair.
  if [ -f "$SMALL_PROXY_PID_FILE" ]; then
    kill "$(cat "$SMALL_PROXY_PID_FILE")" 2>/dev/null && stopped=0
    rm -f "$SMALL_PROXY_PID_FILE"
  fi
  if [ -f "$SMALL_PID_FILE" ]; then
    kill "$(cat "$SMALL_PID_FILE")" 2>/dev/null && stopped=0
    rm -f "$SMALL_PID_FILE"
  fi
  pkill -f "$SERVER_TAG --model.*--port $SMALL_PORT" 2>/dev/null && stopped=0
  pkill -f "$SERVER_TAG --model.*--port $SMALL_UPSTREAM_PORT" 2>/dev/null && stopped=0
  # Proxy first (front), then server (upstream).
  if [ -f "$PROXY_PID_FILE" ]; then
    kill "$(cat "$PROXY_PID_FILE")" 2>/dev/null && stopped=0
    rm -f "$PROXY_PID_FILE"
  fi
  pkill -f "mlx_repair_proxy.py" 2>/dev/null && stopped=0
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null && stopped=0
    rm -f "$PID_FILE"
  fi
  # Belt and suspenders: kill the server by pattern on either possible port.
  pkill -f "$SERVER_TAG --model.*--port $PORT" 2>/dev/null && stopped=0
  pkill -f "$SERVER_TAG --model.*--port $UPSTREAM_PORT" 2>/dev/null && stopped=0
  if [ "$stopped" -eq 0 ]; then echo "Stopped."; else echo "Not running."; fi
}

cmd_status() {
  if is_running; then
    echo "running  front=$PORT  model=$MODEL"
    if [ "$MLX_PROXY" != "0" ]; then
      echo "proxy    on (repair=${MLX_PROXY_REPAIR:-1})  upstream=$SERVE_PORT"
      [ -f "$PROXY_PID_FILE" ] && echo "  proxy-pid=$(cat "$PROXY_PID_FILE")  log=$PROXY_LOG_FILE"
    else
      echo "proxy    off (MLX_PROXY=0; opencode -> server directly)"
    fi
    [ -f "$PID_FILE" ] && echo "  server-pid=$(cat "$PID_FILE")  log=$LOG_FILE"
    if [ "$MLX_SMALL" != "0" ]; then
      if port_listening "$SMALL_PORT"; then
        echo "small    on  front=$SMALL_PORT  model=$SMALL_MODEL  (opencode small_model / titles)"
      else
        echo "small    on (MLX_SMALL=1) but :$SMALL_PORT not listening — 'make mlx-pull' / check $SMALL_LOG_FILE"
      fi
    else
      echo "small    off (MLX_SMALL=0; titles use the main model)"
    fi
    if [ "$MLX_OTEL" != "0" ]; then
      if jaeger_listening; then
        echo "tracing  Jaeger up  OTLP=$OTEL_ENDPOINT  UI=http://127.0.0.1:$OTEL_UI_PORT"
      else
        echo "tracing  on (MLX_OTEL=1) but Jaeger not listening — install/start it (see 'up' hint)"
      fi
      echo "  env: source $OTEL_ENV_FILE  before launching opencode"
    else
      echo "tracing  off (MLX_OTEL=0)"
    fi
  else
    echo "stopped  (model=$MODEL front=$PORT proxy=$([ "$MLX_PROXY" != 0 ] && echo on || echo off) tracing=$([ "$MLX_OTEL" != 0 ] && echo on || echo off))"
  fi
}

cmd_serve() {
  export HF_HUB_OFFLINE=1
  local path
  path="$(resolve_local_path)" || {
    echo "error: weights for $MODEL not present at $TARGET — run 'make mlx-pull' first." >&2
    return 2
  }
  # Foreground = the raw mlx_lm.server on PORT, WITHOUT the repair proxy (one
  # process, Ctrl-C to stop). Use 'up'/'down' for the proxied background setup.
  echo "Serving $MODEL on http://127.0.0.1:$PORT/v1 (offline, no repair proxy; Ctrl-C to stop)"
  exec uvx $UVX_OFFLINE_FLAG --from "mlx-lm==$MLX_LM_VERSION" "$SERVER_TAG" \
    --model "$path" --host 127.0.0.1 --port "$PORT"
}

cmd_opencode_config() {
  local cfg_dir="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}"
  local cfg="$cfg_dir/opencode.json"
  local rules="$cfg_dir/mlx-gemma-rules.md"
  mkdir -p "$cfg_dir"
  # TODO item 12, task C + item 13, thread 2 — a read-range rules file opencode
  # layers on top of its system prompt (referenced via `instructions`, always
  # resident). Bounds per-turn context from tool output: a wholesale large-file
  # read blew one turn to 16k tokens / 252s, and prefill dominates wall-clock on
  # this stack. Item-13 diet (conservative): only the ALWAYS-NEEDED read-range
  # discipline stays resident here; the situational edit-pattern / detailed
  # read-paging guidance moves to the on-demand `coding-discipline` skill below,
  # so it is no longer in every turn's prefix. Regenerated each run.
  cat >"$rules" <<'RULES'
# Local Gemma coding-agent rules (generated by scripts/mlx.sh opencode-config)

This stack's wall-clock is **prefill-dominated** (~0.4 tok/s cold) and a 16 GB M1
hits a ~40–50K-token Metal-OOM cliff. Keep every turn's prompt small.

## Reading files (always)
- NEVER read a whole file when you only need part of it. Use the `read` tool's
  `offset`/`limit` to read a bounded RANGE of lines.
- Narrow with `grep`/`glob` FIRST, then `read` a small window around the match.

For detailed read-paging and edit patterns, load the `coding-discipline` skill.
RULES

  # TODO item 13, thread 1 — the separate opencode-native coding-skill set. Lives
  # under $cfg_dir/skill/<name>/SKILL.md and is registered via the config
  # `skills.paths` entry below. opencode loads a skill's BODY only when the model
  # invokes the `skill` tool for it; only the short description is resident. This
  # is the on-demand destination for situational guidance evicted from the always
  # -resident system prompt (thread 2). Regenerated from scratch each run (the dir
  # is wiped first) so it is idempotent and MLX_SKILLS=0 leaves no residue.
  local skills_dir="$cfg_dir/skill"
  local skill_name="coding-discipline"
  rm -rf "$skills_dir/$skill_name"
  if [ "$MLX_SKILLS" != "0" ]; then
    mkdir -p "$skills_dir/$skill_name"
    cat >"$skills_dir/$skill_name/SKILL.md" <<'SKILL'
---
name: coding-discipline
description: Detailed read-paging, search, and edit patterns for the local Gemma + MLX stack. Load when reading a large file, paging through code, or making an edit, to keep each turn's prompt small (prefill-dominated, ~40-50K-token OOM ceiling).
---

# Coding discipline (local Gemma + MLX)

This stack is prefill-bound and OOM-prone on 16 GB. The resident rules already
say "read ranges, grep first"; this skill is the detailed how-to, loaded only
when you actually need it so it costs nothing on turns that don't.

## Reading / paging large files
- Use `grep` to find the relevant line numbers, THEN `read` with `offset`/`limit`
  a small window (~80-120 lines) around them. Do not read top-to-bottom.
- The `read` tool hard-caps each call (line + column cap) and prints a footer like
  `(rtk: lines 1-1500 of 5001; capped at 1500 lines, use offset=1501 to continue)`.
  When you see it, page on with the suggested `offset=` value — never try to defeat
  the cap with a huge `limit` (it is clamped).
- Prefer `glob` to locate files over reading whole directories.

## Edits
- Read the exact bounded range you intend to change BEFORE editing it.
- Make small, scoped edits; do not rewrite whole files.
- After editing, re-read only the changed range to confirm, not the whole file.

## Service automation (CLI-only invariant)
- This repo wraps each external service in a CLI under `src/<service>/`. If a task
  needs Notion / Google Calendar / inbox data, call the service CLI
  (`uv run notion ...`, `uv run gcal ...`, `uv run inbox ...`) — never an inlined
  remote API call. See CLAUDE.md and docs/services.md.
SKILL
  fi

  MLX_PORT="$PORT" MLX_SERVED_ID="$TARGET" OTEL_PLUGIN_FILE="$OTEL_PLUGIN_FILE" \
    MLX_SMALL="$MLX_SMALL" MLX_SMALL_PORT="$SMALL_PORT" MLX_SMALL_SERVED_ID="$SMALL_TARGET" \
    MLX_SMALL_MODEL="$SMALL_MODEL" MLX_RULES_FILE="$rules" \
    MLX_SKILLS="$MLX_SKILLS" MLX_SKILLS_DIR="$skills_dir" \
    python3 - "$cfg" <<'PY'
import json, os, sys
cfg_path = sys.argv[1]
port = os.environ["MLX_PORT"]
# mlx_lm.server has no model-name alias flag: it serves the model under the
# exact path passed to --model, and GET /v1/models reports that path as the id.
# opencode must address it by that id or mlx_lm tries to resolve it as a HF repo
# (which fails offline). So the model_id IS the on-disk weights path. This is
# correct whether the repair proxy is on or off, and after the proxy is removed.
model_id = os.environ["MLX_SERVED_ID"]
data = {}
if os.path.exists(cfg_path):
    try:
        with open(cfg_path) as f:
            data = json.load(f)
    except Exception:
        data = {}
data.setdefault("$schema", "https://opencode.ai/config.json")
# OpenTelemetry tracing plugin (session/llm/tool spans -> OTLP -> Jaeger). The
# plugin reads its OTLP endpoint from env (OPENCODE_OTLP_*), which `up` writes
# to opencode-otel.env for you to source. We load a PATCHED local copy by
# absolute path when it exists (flush + per-session trace grouping; see
# _vendor_otel_plugin / patch_otel_plugin.py), else the npm name so opencode
# fetches it on first launch — after which 'make mlx-jaeger-up' vendors + repoints
# to the local copy. See docs/opencode-local.md (tracing).
otel_npm = "@devtheops/opencode-plugin-otel"
vendored = os.environ.get("OTEL_PLUGIN_FILE", "")
otel_plugin = vendored if vendored and os.path.exists(vendored) else otel_npm
plugins = data.setdefault("plugin", [])
plugins[:] = [p for p in plugins if p not in (otel_npm, vendored)]
plugins.append(otel_plugin)
prov = data.setdefault("provider", {})
prov["mlx-local"] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Local MLX (Gemma 4 QAT)",
    "options": {"baseURL": f"http://127.0.0.1:{port}/v1", "apiKey": "not-needed"},
    # Custom providers do NOT auto-pull token limits from models.dev (only
    # standard providers do), so set them explicitly or opencode falls back to
    # its own defaults. 32k context / 8k output is a safe E4B-on-16GB tuning;
    # raise on bigger hardware. See docs/opencode-local.md (Tool-call reliability).
    "models": {model_id: {
        "name": "Gemma 4 (local MLX, QAT)",
        "limit": {"context": 32768, "output": 8192},
    }},
}

# TODO item 12, task A (path 2) — point opencode's small_model / title slot at a
# tiny Gemma 3 270M QAT model on its own port, so title-gen stops contending with
# the main model for the single Metal GPU at session start. A separate provider
# (own baseURL) because mlx_lm.server serves one model per port. Removed when
# MLX_SMALL=0. See docs/small-model-research.md + docs/opencode-local.md.
small_id = os.environ.get("MLX_SMALL_SERVED_ID", "")
if os.environ.get("MLX_SMALL", "1") != "0" and small_id:
    small_port = os.environ["MLX_SMALL_PORT"]
    prov["mlx-small"] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Local MLX (Gemma 3 270M QAT — title slot)",
        "options": {"baseURL": f"http://127.0.0.1:{small_port}/v1", "apiKey": "not-needed"},
        "models": {small_id: {
            "name": "Gemma 3 270M (local MLX, QAT, title slot)",
            # Titles are short; a small window keeps the title call's own prefill
            # trivial. NOT used for the coding loop.
            "limit": {"context": 8192, "output": 512},
        }},
    }
    data["small_model"] = f"mlx-small/{small_id}"
else:
    # MLX_SMALL=0 — drop the slot so opencode reuses the main model for titles.
    prov.pop("mlx-small", None)
    data.pop("small_model", None)

# TODO item 12, task B — slim the cold-start prefill prefix. 18.6 KB system
# prompt + 11 tool definitions = ~9447 tokens prefilled cold (~103s) every fresh
# session. Disable tools the local Gemma rarely uses well / that are useless
# offline, cutting their definitions out of the prefix. Scored on latency only;
# the core edit/shell/read/search loop stays intact (tool-call round-trip).
data["tools"] = {
    "webfetch": False,   # fully offline — webfetch can never succeed here
    "task": False,       # subagent orchestration is unreliable on a small local model
    "patch": False,      # redundant with edit/write; large schema
    "todowrite": False,  # todo bookkeeping — overhead, low value for the local loop
    "todoread": False,
    # TODO item 13 fix — registering skills.paths (thread 1) silently drops the
    # write-capable tools (edit/write/list) from the build agent's toolset in
    # opencode 1.17.7, so the model can read/grep/bash but never patch ("no-edit"
    # — caught by harness-eval, invisible to latency/token measurements). Pin the
    # core edit/read/search loop ON explicitly so skill registration can't strip
    # it. A/B + harness-eval confirmed: skills on + these enables → edit returns.
    "edit": True,
    "write": True,
    "list": True,
    "read": True,
    "grep": True,
    "glob": True,
    "bash": True,
}

# TODO item 12, task C — layer the read-range rules file on top of the system
# prompt (instructions are appended, not a replacement, so default tool guidance
# is preserved — unlike a per-agent `prompt` override).
rules_file = os.environ.get("MLX_RULES_FILE", "")
if rules_file:
    instr = data.setdefault("instructions", [])
    if rules_file not in instr:
        instr.append(rules_file)

# TODO item 13, thread 1 — register the separate opencode-native coding-skill
# dir via `skills.paths`. opencode loads a skill's body only when the model
# invokes the `skill` tool, so this surface is non-resident (only descriptions
# sit in the prompt). Idempotent: we SET the path once (membership-guarded), and
# fully remove the skills key when MLX_SKILLS=0 so the disable knob leaves no
# residue. The skill markdown itself is (re)generated by the shell above.
skills_dir = os.environ.get("MLX_SKILLS_DIR", "")
if os.environ.get("MLX_SKILLS", "1") != "0" and skills_dir:
    sk = data.setdefault("skills", {})
    paths = sk.setdefault("paths", [])
    if skills_dir not in paths:
        paths.append(skills_dir)
else:
    # MLX_SKILLS=0 — drop just our path; remove the whole key if nothing is left.
    sk = data.get("skills")
    if isinstance(sk, dict):
        paths = sk.get("paths")
        if isinstance(paths, list) and skills_dir in paths:
            paths.remove(skills_dir)
        if not sk.get("paths") and not sk.get("urls"):
            data.pop("skills", None)
        elif isinstance(paths, list) and not paths:
            sk.pop("paths", None)

with open(cfg_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"Wrote opencode provider 'mlx-local' + otel plugin -> {cfg_path}")
if data.get("small_model"):
    print(f"Title slot -> {data['small_model']} (small_model)")
print(f"Trimmed tools: {', '.join(k for k,v in data['tools'].items() if v is False)}")
sk = data.get("skills", {}).get("paths")
print(f"Skill paths: {', '.join(sk) if sk else '(none — MLX_SKILLS=0)'}")
print(f"Select the coder with:  opencode --model mlx-local/{model_id}")
PY

  # TODO item 13, thread 3 — write the read-cap DEFAULTS into a generated env file
  # the opencode shell function sources before launch (the cap LOGIC stays in the
  # tracked .opencode/tools/read.ts). Regenerated from scratch; removed entirely
  # when MLX_READ_CAP=0 so the disable knob leaves no residue (the tool then uses
  # its own built-in defaults, which equal these).
  local capenv="$cfg_dir/mlx-read-cap.env"
  rm -f "$capenv"
  if [ "$MLX_READ_CAP" != "0" ]; then
    cat >"$capenv" <<EOF
# Read-tool hard-cap defaults — written by scripts/mlx.sh opencode-config (item 13).
# Source before launching opencode (or let the 'opencode' shell function source it):
#   source $capenv
export READ_MAX_LINES=$MLX_READ_MAX_LINES
export READ_MAX_COLUMNS=$MLX_READ_MAX_COLUMNS
EOF
    echo "Read-cap defaults -> $capenv (READ_MAX_LINES=$MLX_READ_MAX_LINES READ_MAX_COLUMNS=$MLX_READ_MAX_COLUMNS)"
  fi

  _vendor_otel_plugin
}

# Bring only Jaeger up (and write the opencode OTLP env file) — the tracing half
# of `up`, without the model server. Useful to start observability on its own.
# Non-fatal if no Jaeger binary is on PATH (see _start_jaeger for the hint).
cmd_jaeger_up() {
  _write_otel_env
  _start_jaeger
  _vendor_otel_plugin
  echo "Source $OTEL_ENV_FILE before launching opencode; spans at http://127.0.0.1:$OTEL_UI_PORT."
}

# Bring only Jaeger down (the one this script started; see _stop_jaeger).
cmd_jaeger_down() {
  _stop_jaeger && echo "Stopped Jaeger." || echo "No managed Jaeger running."
}

case "${1:-}" in
  pull)            cmd_pull ;;
  up)              cmd_up ;;
  down)            cmd_down ;;
  status)          cmd_status ;;
  serve)           cmd_serve ;;
  opencode-config) cmd_opencode_config ;;
  jaeger-up)       cmd_jaeger_up ;;
  jaeger-down)     cmd_jaeger_down ;;
  ""|-h|--help)
    grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '1,40p'
    ;;
  *)
    echo "unknown command '$1' — try: pull | up | down | status | serve | opencode-config | jaeger-up | jaeger-down" >&2
    exit 2 ;;
esac
