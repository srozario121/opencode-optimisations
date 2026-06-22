# Full Jaeger tracing setup (opencode → Jaeger)

A complete, self-contained guide to standing up **distributed tracing** for the
local opencode + Gemma stack: installing Jaeger, the **custom plugins/patches**
that make opencode actually export usable spans, and how to read the result.
Everything runs on `127.0.0.1` — no telemetry leaves the machine.

For the narrative background and design rationale see the *Tracing* section of
[`opencode-local.md`](opencode-local.md); **this page is the operational setup
reference.**

---

## Why this is more than "turn tracing on"

opencode emits **no telemetry natively**, and the community OTLP plugin that adds
it has two behaviours that make it unusable as-shipped against opencode's
process model. So a working setup needs **four** pieces wired together:

| Piece | What it is | Why it's needed |
|---|---|---|
| **Jaeger** (all-in-one) | OTLP collector + storage + UI | the trace backend; receives spans, renders them |
| **`@devtheops/opencode-plugin-otel`** | third-party opencode plugin | opencode's only way to emit session/llm/tool spans |
| **`patch_otel_plugin.py`** (2 patches) | our local patcher | makes the plugin actually flush spans **and** group them per session |
| **repair proxy span** | our `mlx_repair_proxy.py` | captures the **system prompt**, which the plugin structurally cannot |

```
                                   x-session-id header
 opencode ──────────────► repair proxy ──────────────► mlx_lm.server
   │  (chat requests)          │ emits mlx.chat.completions span
   │                           │ (system prompt)        service: mlx-proxy
   │ patched otel plugin       │
   │ emits session/llm/tool ───┤
   │ spans  service: opencode  │
   ▼                           ▼
   └──────── OTLP /v1/traces ─────────►  Jaeger  ◄── UI http://127.0.0.1:16686
              (:4318 HTTP / :4317 gRPC)
```

Because the plugin **and** the proxy both derive the trace id the same way
(`sha256(sessionID)[:32]`), every span from one opencode session — `opencode.*`
plus the proxy's `mlx.chat.completions` — collapses into **one Jaeger trace per
session**.

---

## 1. Install Jaeger

`mlx.sh` auto-detects a Jaeger binary on `PATH` (Jaeger **v2** `jaeger`, or **v1**
`jaeger-all-in-one`). Pick one:

**Binary (recommended for fully-offline use).** Download the all-in-one release
for your platform and put it on `PATH`:

```bash
# https://github.com/jaegertracing/jaeger/releases/latest
#   v2: the binary is `jaeger`           (OTLP enabled by default)
#   v1: the binary is `jaeger-all-in-one` (mlx.sh passes --collector.otlp.enabled)
```

**Docker (no local binary needed).** Start it yourself, then use `MLX_OTEL=1`
without letting `mlx.sh` manage the lifecycle:

```bash
docker run -d --name jaeger \
  -e COLLECTOR_OTLP_ENABLED=true \
  -p 16686:16686 \
  -p 4318:4318 \
  -p 4317:4317 \
  jaegertracing/all-in-one
```

> Jaeger all-in-one keeps spans **in memory** — stopping it discards every
> collected trace. That's fine for a local dev loop; export to a persistent
> backend if you need history.

### Ports

| Port | Purpose | Override |
|---|---|---|
| `16686` | Jaeger Web UI | `MLX_OTEL_UI_PORT` |
| `4318` | OTLP **HTTP**/protobuf receiver (default transport) | `MLX_OTEL_HTTP_PORT` |
| `4317` | OTLP **gRPC** receiver | `MLX_OTEL_GRPC_PORT` |

---

## 2. Bring tracing up

The whole stack (model server + proxy + Jaeger + patched plugin) comes up with:

```bash
make mlx-up
```

To run **only** the tracing backend (no model server) — handy when opencode is
already pointed at a running server:

```bash
make mlx-jaeger-up         # start Jaeger + vendor/patch the plugin + write the env file
make mlx-jaeger-down       # stop the Jaeger this script started
```

`make mlx-jaeger-up` does three things idempotently:

1. starts a local Jaeger if one isn't already listening (non-fatal if no binary —
   it warns and continues; opencode will export once a Jaeger appears);
2. **vendors + patches** the otel plugin (see §3);
3. writes the OTLP env vars opencode's plugin reads to
   `~/.config/opencode-optimisations/opencode-otel.env`.

### Source the env file before launching opencode

`mlx.sh` runs the **model server**, not opencode, so it can't inject env into
opencode's process. You must `source` the generated env file in the shell that
launches opencode:

```bash
source ~/.config/opencode-optimisations/opencode-otel.env
opencode --model mlx-local/<model>
```

The file exports:

```bash
export OPENCODE_OTLP_ENDPOINT=http://127.0.0.1:4318
export OPENCODE_OTLP_PROTOCOL=http/protobuf   # or grpc
```

---

## 3. The custom plugins / patches

### 3a. The plugin itself

opencode discovers the OTLP exporter from `opencode.json`'s `plugin` array.
`make mlx-opencode-config` adds it for you. The plugin
(`@devtheops/opencode-plugin-otel`) exports three span kinds:

- `opencode.session` — one per session
- `opencode.llm` — one per model turn
- `opencode.tool.*` — one per tool invocation

It reads its endpoint from the `OPENCODE_OTLP_*` env vars above.

### 3b. Why we vendor a **patched local copy**

opencode caches the npm plugin under `@latest` and may silently re-fetch it,
reverting any in-place edit. So instead of editing the cached file, `mlx.sh`
(`_vendor_otel_plugin`) copies it to a **local, never-re-fetched** path and
points `opencode.json` at that absolute path, dropping the npm entry so only the
patched copy loads (loading both would duplicate every span):

```
~/.config/opencode-optimisations/opencode-plugin-otel/index.js
```

The patcher `scripts/patch_otel_plugin.py` is **idempotent** and **exits loudly
if an expected anchor moves**, so an upstream change is noticed rather than
silently skipped. It applies two patches:

**Patch 1 — span flush (`BatchSpanProcessor` → `SimpleSpanProcessor`).**
The plugin batches spans and only flushes on a clean `SIGINT`/`SIGTERM`/
`beforeExit`. opencode runs plugins inside its **server** process, which is torn
down without firing those hooks — so the batch queue is discarded and **Jaeger
never receives a span**, even though the plugin's logs/metrics (shorter
intervals) still arrive, making the UI look "connected but empty". Swapping to
`SimpleSpanProcessor` exports each span the instant it ends.

**Patch 2 — per-session trace grouping.**
Out of the box every span starts from a fresh root context with a *random* trace
id, so one session scatters across many Jaeger traces. The patch seeds each span
at the single `ctx.rootContext()` seam with a **deterministic** trace id derived
from the session id — `sha256(sessionID)[:32]` — the **same** derivation the
repair proxy uses. The seeding is wrapped in try/catch: if the trace API shape
ever changes it degrades to per-span behaviour rather than breaking opencode.

> **If the plugin isn't in the cache yet:** launch opencode once (it fetches the
> npm package), then re-run `make mlx-jaeger-up` to vendor + repoint.

**Disable the patches** with `MLX_OTEL_PATCH=0` (falls back to the unpatched npm
plugin — expect empty/scattered traces). Both patches are **temporary**
workarounds; remove once upstream flushes on shutdown and exposes per-session
context (<https://github.com/DEVtheOPS/opencode-plugin-otel>).

### 3c. System-prompt capture (repair-proxy span)

The otel plugin **cannot record the system prompt** — opencode's plugin API
never exposes it (`chat.message` yields only user parts; `chat.params` only
sampling params). The one component that sees the full request (including
`messages[0].role == "system"`) is the **repair proxy**. With tracing on,
`mlx.sh` wires the proxy to emit one extra OTLP span per chat request:

- span `mlx.chat.completions`, service **`mlx-proxy`**
- attributes: `gen_ai.system.message` / `llm.system_prompt` + the full
  `llm.input_messages` array, large attributes truncated at
  `MLX_PROXY_OTEL_MAX_ATTR` (128 KiB)
- trace id from `sha256(sessionID)[:32]` (the proxy reads opencode's
  `x-session-id` header), so it lands in the **same** trace as that session's
  `opencode.*` spans

Emission is best-effort: stdlib-only OTLP/HTTP JSON on a daemon thread, fired in
a `finally` so it survives upstream errors and never blocks or breaks a turn.

> Caveat: this lives in the **temporary** repair proxy, so it disappears under
> `MLX_PROXY=0` or once the proxy is removed. It is the only request-interception
> point we own — opencode talks to `mlx_lm.server` directly otherwise.

---

## 4. View the traces

```bash
open http://127.0.0.1:16686
```

- **Service** dropdown → `opencode` (plugin spans) and `mlx-proxy` (system-prompt
  spans). Both appear once at least one session has run.
- Open a session's unified trace by searching the **`session.id`** tag, or by
  trace id `sha256(<session-id>)[:32]`.
- A single trace shows `opencode.session` → `opencode.llm` / `opencode.tool.*`
  **and** the proxy's `mlx.chat.completions` carrying the system prompt.

---

## 5. Configuration reference

### Stack-level (read by `mlx.sh`)

| Env var | Default | Effect |
|---|---|---|
| `MLX_OTEL` | `1` | master switch for the whole tracing block (`0` disables) |
| `MLX_OTEL_PATCH` | `1` | apply the two plugin patches (`0` = unpatched npm plugin) |
| `MLX_OTEL_PROTOCOL` | `http/protobuf` | OTLP transport; set `grpc` for the gRPC receiver |
| `MLX_OTEL_HTTP_PORT` | `4318` | OTLP HTTP receiver port |
| `MLX_OTEL_GRPC_PORT` | `4317` | OTLP gRPC receiver port |
| `MLX_OTEL_UI_PORT` | `16686` | Jaeger UI port |
| `MLX_OTEL_ENDPOINT` | derived | override the full OTLP endpoint URL |

### Proxy span (read by `mlx_repair_proxy.py`)

| Env var | Default | Effect |
|---|---|---|
| `MLX_PROXY_OTEL` | `0` (wired to `1` by `mlx.sh` when `MLX_OTEL=1`) | emit the system-prompt span |
| `MLX_PROXY_OTEL_ENDPOINT` | `http://127.0.0.1:4318` | where the proxy POSTs `/v1/traces` |
| `MLX_PROXY_OTEL_SERVICE` | `mlx-proxy` | service name shown in Jaeger |
| `MLX_PROXY_OTEL_MAX_ATTR` | `131072` | max attribute size before truncation |
| `MLX_PROXY` | `1` | the repair proxy itself (`0` removes the system-prompt span entirely) |

### opencode env (written to `opencode-otel.env`, you `source` it)

| Env var | Effect |
|---|---|
| `OPENCODE_OTLP_ENDPOINT` | endpoint the opencode plugin exports to |
| `OPENCODE_OTLP_PROTOCOL` | `http/protobuf` or `grpc` |

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| UI shows the service but **no spans** | Plugin batching wasn't patched — ensure `MLX_OTEL_PATCH=1` and that `opencode.json` points at the **vendored** `index.js`, not the npm name. |
| **No `opencode` service** at all | The env file wasn't sourced in opencode's shell — `source ~/.config/opencode-optimisations/opencode-otel.env` before `opencode`. |
| One session **scattered across many traces** | Per-session grouping patch missing (running the unpatched npm plugin). Re-run `make mlx-jaeger-up`. |
| `patch_otel_plugin: anchor missing` | Upstream plugin changed shape — the patcher refuses to patch blindly. Check the plugin version; update the anchors. |
| `opencode otel plugin not in cache yet` | Launch opencode once so it fetches the npm package, then re-run `make mlx-jaeger-up`. |
| **No `mlx-proxy` spans** | Running with `MLX_PROXY=0`, or `MLX_PROXY_OTEL` unset — only the proxy can record the system prompt. |
| Want to reset everything | `make mlx-jaeger-down` (memory-only storage clears on stop). |
