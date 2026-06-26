# Local coding agent: opencode + Gemma 4 QAT via MLX

A **zero-cost, fully-local** coding loop that complements Claude Code:
[opencode](https://github.com/sst/opencode) (an open-source terminal coding
agent) driving a local **Gemma 4 QAT** model served on-device through Apple's
**MLX** stack. No code or prompt leaves the machine.

The shape here is a `scripts/` launcher, `make` targets, and this doc — no
importable package. opencode (an external binary) talks to a **local** MLX
server on `127.0.0.1`; nothing leaves the machine. (This stack was originally
developed inside a larger personal automation toolkit and lifted out into its
own repository.)

> **Related:** [`opencode-config.md`](opencode-config.md) — config-file best
> practices (compaction, rules/AGENTS.md, observability, token frugality) tuned
> for this local setup. [`.opencode/README.md`](../.opencode/README.md) — the
> custom `read`/`grep` tools that reduce tool-output tokens.

## What ships

| Piece | Purpose |
|---|---|
| `scripts/mlx.sh` | controller: `pull` / `up` / `down` / `status` / `serve` / `opencode-config` / `jaeger-up` / `jaeger-down` |
| `make mlx-pull` | one-time weight download into the Hugging Face cache |
| `make mlx-up` / `make mlx-down` | **spin the server up / down** (background; `up` also brings up tracing) |
| `make mlx-status` | is it running, on what port / model |
| `make mlx-serve` | run in the **foreground** (Ctrl-C to stop) — for debugging |
| `make mlx-opencode-config` | install the opencode provider config |
| `make mlx-jaeger-up` / `make mlx-jaeger-down` | **tracing backend on its own** — Jaeger up / down without the model server |

## Pinned model

| | |
|---|---|
| **Model** | `mlx-community/gemma-4-E4B-it-qat-4bit` |
| **Revision** | `0f35c6f6d386f7f74e628bd7c6526ce531212300` |
| **Server (mlx-lm)** | `mlx-lm==0.31.3`, run isolated via `uvx` |

**Why QAT.** [Quantization-Aware Training](https://blog.google/innovation-and-ai/technology/developers-tools/quantization-aware-training-gemma-4/)
bakes the 4-bit quantization into training, so the int4 weights keep much more
quality than a standard post-training-quantized 4-bit model — the right choice
for running locally at low memory.

**Why the E4B (and not the 12B/26B/31B).** This machine is a **16 GB M1
MacBook Air**. Sizing the QAT 4-bit variants by resident weights:

| QAT 4-bit variant | weights | fits 16 GB? |
|---|---|---|
| `gemma-4-E2B-it-qat-4bit` | ~4.0 GB | yes, lots of headroom (lighter/lower quality) |
| **`gemma-4-E4B-it-qat-4bit`** | **~6.3 GB** | **yes — the committed default: best quality with safe headroom** |
| `gemma-4-12B-it-qat-4bit` | ~10.2 GB | too tight on 16 GB (OOM risk under memory pressure) — needs ≥24 GB |
| `gemma-4-26B-A4B` / `31B` QAT | ~15 GB+ | no — needs a 32 GB+ machine |

To try a bigger size on a larger machine, override the model (see below); E4B
stays the committed default for the 16 GB Air.

### Why mlx-lm runs isolated via `uvx` (not a project dependency)

`mlx-lm` requires `transformers>=5`, but the **inbox** privacy stack pins
`transformers<5` (tied to its pinned PII-model corpus — bumping it is out of
scope and risky). Because `mlx-lm` is *launched as a subprocess and never
imported* by any `src/` service, `scripts/mlx.sh` runs it with
`uvx --from mlx-lm==0.31.3`, giving it its own isolated environment. Its
`transformers>=5` can never perturb the inbox models. (See the note in
`pyproject.toml`.) Requires `uv` on `PATH`; nothing is added to `uv sync`.

## 1. Install opencode

```bash
brew install sst/tap/opencode      # or: npm i -g opencode-ai  (see opencode docs)
opencode --version
```

> **Apple Silicon: install the native arm64 build.** If your shell/Homebrew is
> the Intel one under Rosetta (`sysctl -n sysctl.proc_translated` → `1`, or
> `which brew` → `/usr/local/...`), `brew install opencode` pulls the **x86_64**
> opencode. It runs under Rosetta and prints `CPU lacks AVX support, strange
> crashes may occur`, with noticeably slower startup and occasional flakiness.
> Grab the native binary instead (an arm64-only binary runs natively even from a
> Rosetta shell):
> ```bash
> ver=$(gh api repos/sst/opencode/releases/latest --jq .tag_name)   # e.g. v1.17.7
> curl -fsSL -o /tmp/oc.zip \
>   "https://github.com/anomalyco/opencode/releases/download/$ver/opencode-darwin-arm64.zip"
> unzip -o /tmp/oc.zip -d /tmp/oc && install -m755 /tmp/oc/opencode ~/.local/bin/opencode
> file "$(command -v opencode)"   # must say: Mach-O 64-bit executable arm64
> ```
> Ensure `~/.local/bin` precedes `/usr/local/bin` on `PATH`. No AVX warning ⇒
> native. The first run of a freshly-installed version does a one-time ~60–90 s
> setup; after that, `opencode run` is ~10–15 s per invocation (the TUI keeps a
> persistent server, so only its first turn pays startup).

## 2. Pull the weights (one-time)

```bash
make mlx-pull
```

Downloads `mlx-community/gemma-4-E4B-it-qat-4bit` at the pinned revision into a
plain local directory (`~/.config/opencode-optimisations/mlx-models/`, ~6.3 GB) with a
resume-capable `curl` loop and sha256-verifies the weights. It then **warms uv's
tool cache** for `mlx-lm==0.31.3` so serving can run with `uvx --offline`.
Idempotent — a second run skips already-complete files. This is the **only**
step that touches the network; serving is fully offline (no model-hub *and* no
PyPI egress).

## 3. Spin the server up / down

```bash
make mlx-up          # starts the server in the background on 127.0.0.1:8080
make mlx-status      # running | stopped, with port + model
make mlx-down        # stops it
```

`mlx-up` binds **`127.0.0.1` only** and runs with `HF_HUB_OFFLINE=1`, so there
is no model-hub egress at serve time. If the weights are not in the cache it
exits 2 pointing at `make mlx-pull` — it never downloads at start time. Logs go
to `~/.config/opencode-optimisations/mlx-server.log`; the PID to `~/.config/opencode-optimisations/mlx-server.pid`.

For a foreground server (Ctrl-C to stop), use `make mlx-serve` instead.

### Trying another model size

`scripts/mlx.sh` takes env overrides — handy on a larger machine:

```bash
MLX_MODEL=mlx-community/gemma-4-12B-it-qat-4bit make mlx-pull
MLX_MODEL=mlx-community/gemma-4-12B-it-qat-4bit make mlx-up
# also: MLX_PORT=8090, MLX_REVISION=<sha> (defaults to `main` for overrides)
```

## 4. Point opencode at the local server

```bash
make mlx-opencode-config
```

This installs an `mlx-local` provider into `~/.config/opencode/opencode.json`
(merging, not clobbering, any existing config):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "mlx-local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local MLX (Gemma 4 QAT)",
      "options": {
        "baseURL": "http://127.0.0.1:8080/v1",
        "apiKey": "not-needed"
      },
      "models": {
        "gemma-4-e4b-it-qat-4bit": { "name": "Gemma 4 (local MLX, QAT)" }
      }
    }
  }
}
```

The API key is a dummy — `mlx_lm.server` does not authenticate (and ignores the
`model` field, always serving the one loaded model). Select it in opencode's
TUI with `/models` (it appears as **Local MLX (Gemma 4 QAT)**) or launch with
`opencode --model mlx-local/gemma-4-e4b-it-qat-4bit`.

## 5. Smoke test (fully offline)

With `make mlx-up` running:

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"say hi in one word"}],"max_tokens":16}'
```

Then in opencode: open a repo, ask about a file, and have it make a trivial
edit + run a shell command — all offline.

## Tracing (opencode → Jaeger)

opencode emits no telemetry natively. The `@devtheops/opencode-plugin-otel`
plugin (added to `opencode.json` by `make mlx-opencode-config`) exports
session / llm / tool **spans** over OTLP to a local **Jaeger**, all on
`127.0.0.1` — no egress. `make mlx-up` brings the whole thing up; to run the
tracing backend on its own use `make mlx-jaeger-up` / `make mlx-jaeger-down`.

> **Full setup guide:** [`jaeger-tracing.md`](jaeger-tracing.md) — installing
> Jaeger (binary or Docker), the custom plugins/patches required, the complete
> env-var reference, and troubleshooting. The section below is the rationale.

```bash
make mlx-jaeger-up         # start Jaeger (OTLP :4318/:4317, UI :16686) + write env
source ~/.config/opencode-optimisations/opencode-otel.env   # before launching opencode
opencode --model mlx-local/<model>         # spans now ship to Jaeger
open http://127.0.0.1:16686                 # UI → service: opencode
make mlx-jaeger-down       # stop the Jaeger this script started
```

`mlx.sh` runs the model server, not opencode, so it can't inject env into
opencode's process; instead it writes the OTLP env vars to
`~/.config/opencode-optimisations/opencode-otel.env`, which you **`source` before launching
opencode**. Jaeger's all-in-one keeps spans in memory — stopping it discards
collected traces. Disable the whole tracing block with `MLX_OTEL=0`; override
transport with `MLX_OTEL_PROTOCOL=grpc` / `MLX_OTEL_*_PORT`.

### Vendored, patched plugin (default-on workaround)

opencode loads a **patched local copy** of the otel plugin
(`~/.config/opencode-optimisations/opencode-plugin-otel/index.js`), referenced by absolute path in
`opencode.json`, instead of the npm package. `make mlx-up` / `mlx-jaeger-up` /
`mlx-opencode-config` vendor it from the npm cache and apply two patches via
`scripts/patch_otel_plugin.py` (`_vendor_otel_plugin` in `scripts/mlx.sh`), then
repoint `opencode.json` and drop the npm entry so only the patched copy loads
(loading both would duplicate every span). A local copy is used because opencode
caches the npm plugin under `@latest` and may re-fetch it, reverting any in-place
edit; the vendored file is never re-fetched. The patcher is idempotent and exits
loudly if an expected anchor moves (so an upstream change is noticed). The two
patches:

1. **Span flush** — the plugin batches spans through a `BatchSpanProcessor` that
   only flushes on a clean `SIGINT`/`SIGTERM`/`beforeExit`. opencode runs plugins
   inside its **server** process, torn down without firing those hooks, so the
   batch queue is discarded and **Jaeger never receives a span** — though the
   plugin's logs/metrics (shorter intervals) still arrive, making the UI look
   "connected but empty". Swapped to `SimpleSpanProcessor`, so each span exports
   the instant it ends.

2. **Per-session trace grouping** — see below.

If the plugin isn't in the cache yet, launch opencode once (it fetches the npm
package), then re-run `make mlx-jaeger-up` to vendor + repoint. Disable both
patches with `MLX_OTEL_PATCH=0` (falls back to the npm plugin, unpatched). Remove
once upstream flushes on shutdown and exposes per-session context
(<https://github.com/DEVtheOPS/opencode-plugin-otel>).

### Per-session trace grouping

Out of the box, opencode's spans **scatter**: the plugin starts each session /
llm / tool span from a fresh root context, and opencode's async flow doesn't keep
the session span alive across turns, so nearly every span becomes its own root
with a *random* trace id — one session ends up spread across many Jaeger traces,
and the proxy's system-prompt spans (below) are separate again.

The patch fixes this at the single seam every span passes through
(`ctx.rootContext()`): it seeds each span with a **deterministic trace id derived
from the session id** — `sha256(sessionID)[:32]`, the **same** derivation the
repair proxy uses (it reads the `x-session-id` header opencode sends). Only the
trace id has to match for Jaeger to group spans, so all of a session's
spans — `opencode.session`, `opencode.llm`, `opencode.tool.*`, **and** the
proxy's `mlx.chat.completions` (system prompt) — collapse into **one trace per
session**. Open that trace by searching the `session.id` tag, or by trace id
`sha256(<session-id>)[:32]`. (An inbound W3C `traceparent` still wins if one is
ever present.) The seeding is wrapped in try/catch: if the trace API shape ever
changes it degrades to the original per-span behaviour rather than breaking
opencode.

### System-prompt capture (via the repair proxy)

The otel plugin **cannot record the system prompt**: it hardcodes each LLM span's
`llm.input_messages` to the single latest user turn, because opencode's plugin
API never exposes the system prompt to a plugin (`chat.message` yields only user
message parts; `chat.params` only sampling params). So in Jaeger the `opencode`
spans show the user message but not the system instructions.

The only component that sees the full request — including `messages[0].role ==
"system"` — is the **repair proxy**. When tracing is on (`MLX_OTEL=1`), `make
mlx-up` wires the proxy to emit one extra OTLP span per chat request
(`mlx.chat.completions`, service **`mlx-proxy`**) carrying the system prompt
(`gen_ai.system.message` / `llm.system_prompt`) and the full `llm.input_messages`
array. Emission is best-effort: stdlib-only OTLP/HTTP JSON on a daemon thread,
fired in a `finally` so it survives upstream errors and never blocks or breaks a
turn. Toggle with `MLX_PROXY_OTEL`; point elsewhere with
`MLX_PROXY_OTEL_ENDPOINT`; rename the service with `MLX_PROXY_OTEL_SERVICE`.

**Grouping by session.** opencode never propagates a W3C `traceparent` to the
model, but it *does* send the session id as the `x-session-id` header to this
provider. The proxy derives a stable trace id from it (`sha256(sessionID)[:32]`)
and tags each span with `session.id`. Because the patched plugin uses the **same**
derivation for opencode's own spans (see *Per-session trace grouping* above), the
proxy's `mlx.chat.completions` spans land in the **same single trace** as that
session's `opencode.*` spans — one unified trace per session. (An inbound
`traceparent` still wins if one is ever present.)

> Caveat: this lives in the **temporary** repair proxy, so it disappears if you
> run with `MLX_PROXY=0` or once the proxy is removed (PR #1142). It is the only
> interception point we own — opencode talks to `mlx_lm.server` (a `uvx`
> dependency) directly without it.

## Tool-call reliability

<!-- SMOKE-TEST-FINDINGS -->
**Smoke test (2026-06-16, Gemma 4 E4B QAT, mlx-lm 0.31.3, M1/16 GB):**

- **Q&A round-trip** — `opencode run` against `mlx-local/<model>` answered a
  trivial question correctly, **fully offline** (server `HF_HUB_OFFLINE=1`).
- **Tool-call round-trip** — opencode emitted a structured `write` tool call
  that executed successfully ("Wrote file successfully"); the QAT model's tool
  call parsed cleanly through mlx-lm 0.31.3's `gemma4` parser, so the repair
  proxy **passed through without needing to repair or retry** (empty proxy log).
  The #1125 `ValueError` path did not trigger on these simple calls but remains
  a latent risk on more complex / malformed calls — the proxy is the safety net.
- **Model id gotcha** — `mlx_lm.server` has no name-alias flag; it serves the
  model under the exact path passed to `--model`, and that path **is** the model
  id opencode must use. `make mlx-opencode-config` writes it correctly (the id
  looks like `mlx-local//Users/.../gemma-4-E4B-it-qat-4bit` — the double slash is
  expected). Using a friendlier id would make `mlx_lm` try to resolve it as a
  HuggingFace repo and fail offline.
- **"First call hangs" — found and fixed.** The repair proxy originally forced
  every tool request to a non-streamed upstream call and buffered the whole
  response. opencode streams, so it saw *nothing* until generation finished —
  minutes of silence on long turns, indistinguishable from a hang. Fixed: the
  proxy now passes streaming requests through transparently (verified: 44
  incremental token deltas + `tool_calls` + `[DONE]` for a streamed tool call),
  and only buffers/repairs the non-streaming path where #1125 is fatal.
- **opencode arch — fixed.** The original install was an **x86_64** opencode
  (Intel Homebrew at `/usr/local`) running under Rosetta on the M1, with the
  `CPU lacks AVX support` warning, slow startup, and one flaky no-op run.
  Replaced with the **native arm64 build** (1.17.7) in `~/.local/bin` (see
  install note above) — AVX warning gone, runs native.
- **First-call latency is a one-time setup, not a recurring hang.** Even on
  arm64, the *first* `opencode run` of a freshly-installed version took ~80 s;
  the 2nd/3rd were ~12–14 s. So it's one-time version init (model-db refresh
  etc.), not the harness — the model path is single-digit seconds via curl.
  Steady-state `opencode run` is ~12 s (fresh server per invocation); the TUI's
  persistent server makes follow-up turns snappier.
- **Recommended default** — Gemma 4 E4B QAT is usable for simple edit/Q&A loops
  and streams tool calls correctly on mlx-lm 0.31.3. Keep
  `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` as the fallback if more complex
  tool-calling proves flaky.

opencode's edit/shell loop depends on the model emitting OpenAI-style
`tool_calls`. Gemma 4 emits native tool calls wrapped in `<|tool_call>` …
`<tool_call|>` markers, and the state of `mlx_lm.server`'s translation of those
is **version-specific**:

- **Empty `tool_calls` (mlx-lm issue #1096) — fixed in the pinned 0.31.3.**
  `mlx-lm` 0.31.3 ships a dedicated `mlx_lm/tool_parsers/gemma4.py` that
  recognises the `<|tool_call>`/`<tool_call|>` markers, so calls are surfaced as
  structured `tool_calls` rather than left as raw text in `content`.
- **`ValueError: No function provided.` (issue #1125) — STILL UNFIXED in 0.31.3.**
  When Gemma emits text the parser regex doesn't match as a call (ordinary
  reasoning, or a malformed call), `gemma4.py` *raises* instead of returning an
  empty list, which fails the opencode request. The fix (PR #1142, "return `[]`
  instead of raising") was **not merged** as of the 0.31.3 pin — so expect
  occasional hard failures on unmatched output until a newer release lands.

If the pinned Gemma 4 QAT model proves flaky at tool calls in opencode, the
documented fallback is a tool-call-strong 4-bit MLX model — e.g.
`mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` (~4.1 GB, fits the 16 GB budget):
set `MLX_MODEL` to it for `mlx-pull`/`mlx-up` and re-run `make
mlx-opencode-config`. When bumping `MLX_LM_VERSION`, re-check issue #1125 / PR
#1142 — once that fix ships, the `ValueError` failure mode goes away. Either way
the reliability observed here is recorded honestly.

### Tool-call repair proxy (default-on workaround)

Because #1125 is unfixed in the pin, `make mlx-up` runs a small **tool-call
repair proxy** (`scripts/mlx_repair_proxy.py`, stdlib-only) in front of the
model. opencode always talks to `MLX_PORT` (8080); the proxy forwards to
`mlx_lm.server` on `MLX_UPSTREAM_PORT` (8081), so toggling it never changes the
opencode config. It:

- **Streaming requests pass through transparently**, preserving mlx-lm's native
  token-by-token stream (including its own correct `tool_calls`). opencode
  streams, and 0.31.3 streams tool calls correctly, so there is nothing to
  repair on this path — and buffering it was what made the **first call appear to
  hang** (opencode saw nothing until the whole response finished generating).
- **Non-streaming tool requests are buffered and repaired**: re-parse
  `<|tool_call>…<tool_call|>` text left in `content` into structured `tool_calls`
  (covers #1096 / any regression), and catch the #1125 `ValueError` 500 — retry
  (a fresh sample may parse — only helps at `temperature > 0`) and, on
  exhaustion, return a graceful empty turn so the session survives the 500. The
  #1125 crash is a non-streaming behaviour; in streaming it degrades to a turn
  with no tool call rather than a fatal error.

Non-tool chats and other endpoints pass through untouched (streaming preserved).

**This is temporary.** Disable it any time:

- `MLX_PROXY=0 make mlx-up` — don't run the proxy; opencode talks straight to
  the server on `MLX_PORT`.
- `MLX_PROXY_REPAIR=0 make mlx-up` — keep it in the path but pass through
  unmodified (A/B debugging).

> ⚠️ **Remove the proxy once [PR #1142](https://github.com/ml-explore/mlx-lm/pull/1142)
> is merged and `MLX_LM_VERSION` is bumped past the release that contains it.**
> The proxy's startup banner and `make mlx-up` both print this reminder; `make
> mlx-status` shows whether it's active. After bumping, run with `MLX_PROXY=0`,
> confirm tool calls still work, then delete `scripts/mlx_repair_proxy.py` and
> the proxy wiring in `scripts/mlx.sh`.

## Optional: a least-privilege agent for the local model (template)

A weak local model does best on narrow tasks with a small, safe tool set and a
low temperature. opencode supports per-agent overrides, so you can route the
local Gemma to a focused agent while leaving the default agent on a stronger
(remote) model. This is a **template** — `make mlx-opencode-config` does not
write it; paste it into `~/.config/opencode/opencode.json` under an `agent` key
(it merges alongside the generated `provider` block):

```json
{
  "agent": {
    "local-edit": {
      "description": "Small, precise local edits on the offline Gemma model.",
      "model": "mlx-local/gemma-4-e4b-it-qat-4bit",
      "prompt": "You are a focused code-editing assistant. Make small, precise, well-scoped changes. Prefer reading before editing. Do not run destructive shell commands.",
      "temperature": 0.2,
      "permission": { "edit": "allow", "bash": "ask", "write": "deny" }
    }
  }
}
```

Notes:

- **`model`** is `mlx-local/<model_id>`, where `<model_id>` is the lowercased
  basename of `MLX_MODEL` (what `make mlx-opencode-config` prints). Update it if
  you switch models.
- **`permission`** (`allow`/`ask`/`deny`) is the current mechanism; the older
  boolean `tools` map is deprecated (opencode ~v1.1.1). Tightening tools keeps a
  flaky model from taking risky actions and shrinks its decision surface.
- **`temperature`** low (0.1–0.3) reduces malformed tool calls — relevant given
  the unfixed `ValueError` path above.
- Invoke with `opencode --agent local-edit`.

## Throughput (TODO item 9)

A benchmark-driven evaluation of two ways to raise throughput for this stack on
the 16 GB M1, each adopted only if it beat the current mlx-lm/E4B default.
**Outcome: neither was adopted — the current default stands.** The evidence is
below. Background research: `docs/local-model-throughput-research.md`.

### Benchmark harness

`scripts/mlx_bench.py` (stdlib-only; `make mlx-bench LABEL="…"`) drives any
OpenAI-compatible `127.0.0.1/v1` endpoint and records three metrics:

- **single-shot TTFT** and **single-shot decode tok/s** (small prompt), and
- the **headline metric: multi-turn agentic TTFT** — a growing-prefix
  conversation (a codebase loaded in turn 1, then a chunk added and the whole
  thing resent each turn, ramping ~12K→28K tokens) that mimics the opencode
  loop. With working prefix/KV reuse, per-turn TTFT stays low; without it, TTFT
  climbs as the whole prompt is re-prefilled every turn.

Results append to `~/.config/opencode-optimisations/mlx-bench/results.jsonl`; `make
mlx-bench-summary` prints the comparison table. The loop shape is fixed
(deterministic synthetic context, fixed prompts/turns — see the module
docstring) so every backend is measured identically; the ramp tops out at ~28K
tokens, safely under the ~40–50K Metal-OOM cliff for E4B on 16 GB (a full ramp
to 40K crashes the mlx-lm server — see Troubleshooting).

**Measure the backend directly, not through the repair proxy.** The tool-call
repair proxy buffers the whole response (it must, to repair tool calls), which
defeats streaming TTFT/decode timing. Throughput is therefore benchmarked
against the raw `mlx_lm.server` port (8081 when the proxy is on), not the proxy
port (8080). The proxy is a tool-call concern, separate from prefill throughput.

### Baseline — mlx-lm 0.31.3 / gemma-4-E4B-it-qat-4bit

| metric | value |
|---|---|
| single-shot TTFT | 1.5 s |
| single-shot decode | ~11.7 tok/s |
| agentic turn 1 (cold, ~12K ctx) TTFT | ~68 s |
| agentic turns 2–5 (each +4K ctx) TTFT | ~25 s each |
| server `cached_tokens` / `prompt_tokens` (last turn) | 24430 / 28511 (~86 %) |

**Key finding — the research premise does not reproduce on this stack.** The
research predicted Gemma's sliding-window attention defeats mlx-lm's prefix
cache (full prompt recompute every turn, ~200 s at 40K). Empirically, **prefix
reuse works**: `cached_tokens` grows to cover the prefix, the server log shows
only the *new* chunk being prefilled each turn, and per-turn TTFT stays flat
(~25 s) rather than climbing with total context. The real limiters here are:

1. **slow prefill** (~160 tok/s on E4B/16 GB — so each new context chunk is
   expensive: 4K tokens ≈ 25 s),
2. **slow decode** (~8–12 tok/s, degrading as context grows), and
3. a **Metal-OOM ceiling at ~40–50K tokens** — E4B alone crashes the server
   (`kIOGPUCommandBufferCallbackErrorOutOfMemory`) at that context on 16 GB.

### Approach A — vllm-mlx as the serving backend → not adopted

`vllm-mlx` (waybarrios/vllm-mlx) `0.3.0` advertises prefix caching, a dedicated
Gemma 4 tool-call parser, and an OpenAI-compatible API. It cleared part of the
verification gate but **failed the core requirement**:

- ✅ **offline**: loads the local E4B weights with `--offline` + `HF_HUB_OFFLINE=1`.
- ✅ **Gemma tool-call parser** (`--tool-call-parser gemma4`): emits textbook
  OpenAI `tool_calls` (separated `reasoning_content`, valid JSON args,
  `finish_reason: "tool_calls"`).
- ❌ **usable generation for our model**: vllm-mlx 0.3.0 fails to build the
  optimized text model for the gemma-4-E4B **multimodal QAT** variant
  (`Failed to build TextModel from vlm: '>' not supported between NoneType and
  int`) and falls back to a generic multimodal (MLLM) path. On that path
  generation **stalls** — a tiny 120-token request **times out after 300 s**
  (1 streamed chunk; 504 on non-streaming). opencode needs streaming, so the
  endpoint is unusable, and throughput is *far worse* than baseline, not better.

Per the gate's stop rule, no benchmark was run. vllm-mlx was uninstalled.
(It may suit a non-multimodal model on larger hardware — out of scope here.)

### Approach B — speculative decoding (E2B draft) → not adopted

mlx-lm 0.31.3 supports `--draft-model` / `--num-draft-tokens`. Tested with
**`mlx-community/gemma-4-E2B-it-qat-4bit` @ `42f62737af7a9fd8c1d55d79666c1a217be4e2e2`**
(~4 GB) as the draft for E4B, `--num-draft-tokens 3`. **Failed on three counts:**

- ❌ **output-correctness gate (mlx-lm#846)**: with greedy decoding (temp 0)
  speculative output must equal non-speculative output, but **4 of 5 fixed
  prompts diverged** and the speculative output was corrupted
  (`"a hash map is a a a a data data structure.88 that that that …"`) — the
  known token-skipping bug. This alone disqualifies it.
- ❌ **slower, not faster**: speculative decode measured **~7 tok/s vs ~8.7
  tok/s** non-speculative. E2B (~4 GB) is too large relative to E4B (~6.3 GB)
  to be an effective draft — draft overhead exceeds the savings.
- ❌ **memory-infeasible at scale**: both models resident leave only ~21 % system
  memory free at *tiny* context; the agentic workload (large context) would OOM
  well before 28K, since E4B *alone* already OOMs near 40–50K. Speculative
  decoding also only speeds **decode**, which is the *smaller* cost here — the
  agentic loop is **prefill**-dominated.

Per the plan, no benchmark was run once the correctness gate failed. The E2B
weights were deleted (re-pullable via the id+SHA above); no E2B pin was added to
`scripts/mlx.sh` since the approach was not adopted.

### Decision

**Keep the current default** (mlx-lm 0.31.3 + gemma-4-E4B-it-qat-4bit + the
repair proxy). Neither in-scope approach beat the baseline:
vllm-mlx can't run our multimodal E4B usefully; speculative decoding is
incorrect (mlx#846), slower, and memory-infeasible on 16 GB. The
highest-leverage *unexplored* levers from the research (full-attention model for
prefill, KV-cache quantization) were explicitly out of scope for this item.

**Repair-proxy interplay.** Nothing changed: the proxy stays on for the mlx-lm
default (it works around the Gemma tool-call parser bugs, unrelated to
throughput). Its removal remains tracked under **TODO item 8 / mlx-lm PR #1142**
— this item does not resolve it. vllm-mlx's own `gemma4` parser *would* have
removed the need for the proxy had it been adopted, but it was not.

## Inference engines (TODO item 10)

A broader sequel to item 9: instead of tuning the MLX path, compare **entirely
different inference engines** against the mlx-lm/E4B baseline for serving the
local Gemma 4 E4B QAT model to opencode on this 16 GB M1. Same
adopt-if-better rule, same headline metric (multi-turn agentic TTFT). Framework
survey + per-candidate matrix: `docs/local-inference-engines-research.md`.

**Outcome: no engine beat the baseline on the agentic metric — the mlx-lm/E4B
default stands.** Measured on the **same `scripts/mlx_bench.py` harness, same
params** (turns=5, ctx_base=12000, ctx_step=4000, max_tokens=120) as the item-9
baseline, fully offline, on 2026-06-19:

| engine (config) | gate | 1-shot TTFT | 1-shot t/s | turn-1 TTFT | **reuse TTFT** | agentic wall | tool-calls |
|---|---|---|---|---|---|---|---|
| **mlx-lm 0.31.3 / E4B-it-qat-4bit** (baseline) | — | 1.77 s | 11.6 | 68 s | **25.3 s** | 236 s | via repair proxy |
| **llama.cpp** `llama-server` (build 9700) / E4B QAT q4_0 GGUF | ✅ pass | **0.62 s** | **17.0** | 103 s | **59.9 s** ❌ | 385 s ❌ | ✅ native (valid JSON) |
| **vllm-metal** 0.3.0.dev / E4B (MLX) | ✅ pass † | 3.6 s | 10.3 | 213 s | **169 s** ❌ | 903 s ❌ | degenerate output |
| mlx-openai-server 1.8.1 / E4B-it-qat-4bit (MLX) | ❌ fail | — | — | — | — | — | — |
| SGLang | ❌ N-A | — | — | — | — | — | — |

† vllm-metal only built after fixing a broken CommandLineTools C++ toolchain
(see its subsection); it then passed the gate but lost the benchmark badly.

Headline = **reuse TTFT** (mean TTFT of turns ≥ 2 — the per-turn prefill cost of
the growing agentic prefix). Lower is better; it is the number that decides
adoption (item 9). Raw rows append to `~/.config/opencode-optimisations/mlx-bench/results.jsonl`.

### llama.cpp / `llama-server` → benchmarked, NOT adopted

The only non-MLX engine that cleared the gate and ran. `brew install llama.cpp`
(build 9700, native Metal — `arch -arm64` is required to install since this
machine's shell runs under Rosetta), served the **Google official QAT GGUF**
(see provenance below) with all layers on the Metal GPU (`-ngl 99`), 32K context,
`--jinja`, prompt cache + context checkpoints on.

- ✅ **Gate**: Metal GPU (`device_info: MTL0 Apple M1`), loads the 4-bit QAT
  GGUF, fits 16 GB, OpenAI-compatible `/v1`, fully offline (local `-m` file),
  prefix reuse works (`cached_tokens` 24431/28511, like the baseline).
- ✅ **Tool-calls work natively** (chat format `peg-gemma4`): a real tool request
  returns `finish_reason: tool_calls` with valid JSON args
  (`{"city":"Paris"}`) — the April-2026 malformed-JSON bug (#21316) is fixed in
  build 9700. So llama.cpp needs **no repair proxy** — a genuine advantage over
  the mlx-lm baseline on the tool-call axis.
- ❌ **Loses the headline metric.** It *wins* single-shot TTFT (0.62 s vs 1.77 s)
  and decode (17 vs 11.6 t/s), but the agentic loop is **prefill-dominated**
  (item-9 finding) and llama.cpp's q4_0 Metal prefill is ~2.4× slower than
  mlx-lm's MLX-4bit prefill: reuse TTFT **59.9 s vs 25.3 s**, total agentic wall
  **385 s vs 236 s**, and per-turn TTFT climbs steeply (48→71 s). Flash attention
  (`-fa on`) made no measurable difference (59.91 vs 59.90 s). Faster decode
  cannot rescue a prefill-bound loop.
- **Quant caveat (not apples-to-apples):** q4_0 GGUF is not bit-identical to the
  MLX 4-bit QAT weights, though both derive from Google's QAT lineage. The
  prefill gap is an engine/format property, not a quant artifact, so the verdict
  holds regardless.

### mlx-openai-server → gate fail (cannot generate Gemma)

cubist38/mlx-openai-server 1.8.1 installs and loads the existing MLX E4B weights
on Metal, and its current `--tool-call-parser` list *does* now include `gemma4`
(contrary to the April-2026 research snapshot). But **every generation fails**
with `There is no Stream(gpu, N) in current thread.` — an **open upstream bug
([#312](https://github.com/cubist38/mlx-openai-server/issues/312))**: any
sliding-window model (Gemma 3/4) builds a `RotatingKVCache` whose buffers are
evaluated at load time and bound to the loader thread, but the server generates
on a different worker thread, and MLX streams are thread-affine. Independent of
`--disable-batching`, streaming, and request body; no fixed release exists.
Gemma 4 E4B is a sliding-window model → **unusable, not benchmarkable.**

### vllm-metal → built (toolchain fixed), benchmarked, NOT adopted

The official `vllm-project/vllm-metal` plugin (release `v0.3.0.dev20260618…`,
distinct from item-9's rejected `waybarrios/vllm-mlx` fork) ships a prebuilt Metal
wheel but **requires vLLM core compiled from source** (`vllm 0.23.0`, via
`clang++`).

- **Toolchain blocker (fixed).** The compile first failed because even a trivial
  `#include <cmath>` would not compile (`fatal error: 'cmath' file not found`).
  Root cause: this machine's CommandLineTools (26.3) is in a **broken/partial
  state** — its toolchain libc++ dir
  (`/Library/Developer/CommandLineTools/usr/include/c++/v1`) holds only 4 of the
  ~189 headers, and clang searches that (incomplete) path before the SDK. The
  **complete** libc++ lives in the SDK
  (`…/MacOSX.sdk/usr/include/c++/v1`). Fix (no `sudo`, no CLT reinstall): build
  with `CXXFLAGS="-nostdinc++ -isystem <sdk>/usr/include/c++/v1 -Wno-parentheses"`
  so C++ compiles use the SDK's complete libc++. With that, vLLM 0.23.0 core
  compiled cleanly and the vllm-metal wheel installed. (A proper repair is the
  pending **CLT 26.5** update — `sudo softwareupdate -i …` / reinstall CLT — but
  the build-flag workaround sidesteps it.)
- ✅ **Gate**: loads the existing **MLX 4-bit E4B** weights on the Metal GPU
  (`MLX device set to Device(gpu, 0)`, `PyTorch device set to mps`, model loaded
  in 4.3 s), fits 16 GB (`Metal memory: 17.2 GB total, 11.5 GB available`,
  wired-limit 11.8 GB), OpenAI-compatible `/v1`, fully offline
  (`HF_HUB_OFFLINE=1` + local path), prefix caching + chunked prefill enabled.
  It even handles the multimodal E4B by forcing a **text-only backbone**
  (`Metal: forcing text-only backbone for gemma4`) — where item-9's waybarrios
  fork stalled.
- ❌ **Loses the benchmark, badly.** reuse TTFT **169 s vs 25.3 s** (~6.7×
  worse), turn-1 TTFT **213 s vs 68 s**, agentic wall **903 s vs 236 s** (~3.8×);
  single-shot also worse (TTFT 3.6 s, decode 10.3 t/s). **Plus a reliability
  red flag:** generation collapsed to **2–30 tokens/turn** (baseline emits the
  full 120) — degenerate output, consistent with the startup warning that it
  *forces* the `TRITON_ATTN` backend for Gemma 4's heterogeneous head dims while
  **Triton is not installed** on Metal.
- **Why it loses despite the same MLX backend (prediction confirmed):**
  vllm-metal's serving architecture (chunked prefill in 2048-token windows +
  paged attention) is built for **batched, multi-request throughput** — it adds
  large per-prefill overhead that a single-user growing-prefix loop pays in full,
  far exceeding mlx-lm's direct prefill path. Its strength is irrelevant to this
  workload. **Not adopted.**

### SGLang → not applicable (CUDA-only)

SGLang 0.5.13's mandatory dependencies are CUDA-only —
`cuda-python>=13.0`, `flashinfer_python[cu13]`, `nvidia-cutlass-dsl[cu13]`,
`nvidia-ml-py` — none with macOS/arm64 wheels; uv backtracks to an ancient 0.5.2
that cannot even import, and **no SGLang version ships a Metal GPU backend.** It
cannot be stood up to serve on Apple Silicon. Item 10 required it be gate-tested
even though expected to fail (CUDA-first) — confirmed N-A.

### Survey-only (not benchmarked — see the research doc for the per-candidate matrix)

- **vLLM upstream core, Hugging Face TGI, TensorRT-LLM, ExLlamaV2** — CUDA-centric,
  no Apple-Silicon Metal GPU path (TGI also entered maintenance mode 2025-12-11).
- **Ollama** — now MLX-backed but preview-stage and recommends >32 GB unified
  memory (marginal on 16 GB); being llama.cpp/MLX-based, its throughput is already
  represented by the llama.cpp and mlx-lm rows above.
- **LM Studio** — a GUI app whose engines *are* mlx-lm and llama.cpp; it adds no
  independent engine, so both its paths are already measured directly above.

### GGUF provenance (Google official QAT) + re-pull

The llama.cpp benchmark used Google's **official QAT q4_0 GGUF**, pulled offline
via the same curl-resume + sha256 mechanism as `scripts/mlx.sh pull` and
integrity-verified:

- repo: **`google/gemma-4-E4B-it-qat-q4_0-gguf`**
- revision: `bb3b92e6f031fa438b409f898dd9f14f499a0cb0`
- file: `gemma-4-E4B_q4_0-it.gguf` (5,154,939,136 bytes)
- sha256: `e8b6a059ba86947a44ace84d6e5679795bc41862c25c30513142588f0e9dba1d`

This answers the research's open question (a trustworthy 4-bit **QAT** Gemma 4 E4B
GGUF *does* exist, from Google). No GGUF pin was wired into `scripts/mlx.sh`
because llama.cpp was **not adopted** and `mlx.sh` serves MLX weights, not GGUF —
mirroring item 9's "don't pin the unadopted draft model" decision. The GGUF is
re-pullable from the id + revision + sha256 above if llama.cpp is revisited.

### Decision (item 10)

**Keep the current MLX default** (mlx-lm 0.31.3 + gemma-4-E4B-it-qat-4bit + the
repair proxy). No engine beat the baseline on the headline agentic-TTFT metric:
**llama.cpp** ran but lost (prefill-bound loop, ~1.6× worse wall) despite better
single-shot/decode and native tool-calls; **vllm-metal** built (after the libc++
toolchain fix) and ran but lost far worse (~6.7× reuse TTFT, degenerate output) —
its batched paged-attention design is wrong for a single-user loop;
**mlx-openai-server** failed the viability gate (sliding-window MLX stream bug);
**SGLang** and the rest are CUDA-only / survey-only on this Mac. "Evaluated,
engines surveyed, none beat MLX" is the planned valid terminal state.

**Repair-proxy interplay.** Unchanged — the proxy stays on for the mlx-lm
default. The one engine that removes the need for it (llama.cpp, native
`peg-gemma4` tool-calls) was not adopted for throughput reasons. The item-8
PR #1142 follow-up still owns proxy removal on the mlx-lm path; this item only
**cross-references** it. Notable per-config tool-call findings that inform it:
the Gemma 4 tool-call ecosystem has moved since the April-2026 research snapshot
— llama.cpp build 9700 emits valid tool-calls, and mlx-openai-server now ships a
`gemma4` parser — so PR #1142 may well have landed equivalently upstream; re-check
at item-8 time.

## Harness engineering (TODO item 11)

**Outcome: evaluated, no lever adopted — the item-8 default harness is kept.**
Items 8–10 fixed the model (Gemma 4 E4B QAT) and the serving engine (mlx-lm
0.31.3) and found nothing better. Item 11 turned to the last lever — the
**opencode-side harness** — and asked whether any single configuration change
raises **task pass-rate** (not throughput). A deep-research pass
(`docs/harness-engineering-research.md`) ranked the tunable lever surface and the
top four single-lever changes were each scored against a frozen baseline. **None
moved the pass rate off the floor (0/8 on every config).** The binding
constraint is the fixed model+engine, not the harness — consistent with items
9/10. This is the planned valid terminal state.

### Method

- **Scoring instrument** — `scripts/harness_eval.py` (new; stdlib-only, separate
  from the throughput-only `scripts/mlx_bench.py`). It drives opencode
  **headlessly** (`opencode run`) on a SWE-bench Lite instance, captures the
  model's patch (`git diff` vs base, excluding test + harness files), applies the
  instance's *test* patch, runs the designated tests, and scores **pass/fail**
  SWE-bench-style (every `FAIL_TO_PASS` flips AND every `PASS_TO_PASS` holds).
  Hard **30-min per-instance cap**; **timeout or MLX-server OOM → fail**, reason
  logged, server auto-restarted via `scripts/mlx.sh`, run continues. An
  append-only JSONL ledger + regenerated markdown summary live under
  `~/.config/opencode-optimisations/harness-eval/`.
- **Test environments — native `uv` venvs, no Docker.** The official SWE-bench
  harness is Docker-based (GBs/image, awkward offline on 16 GB). Instead,
  `harness_eval.py prepare` provisions one native venv per instance **once**
  (online), verified by applying the *gold* patch and confirming the full scoring
  predicate holds; venvs + repos are then frozen and reused fully offline. The
  dataset is pulled via the HF datasets-server `/rows` endpoint and cached
  locally (the `/filter` endpoint 500s intermittently).
- **Frozen subset** (`scripts/harness_eval_subset.json`) — **8 `sympy`
  instances**, each pre-screened under ~30K est. context (to dodge the 40–50K
  Metal-OOM cliff) and gold-verified winnable on **Python 3.9** (old sympy uses
  `from collections import Mapping`, gone in 3.10). The subset is sympy-dominant
  by consequence of the no-Docker, offline-install screen: dependency-coupled
  repos (e.g. `flask` → werkzeug/jinja2 version coupling) don't reproduce
  natively and were correctly dropped. The subset is frozen for the experiment.
- **Levers** (`scripts/harness_configs/*.json`) — each lever is an opencode-side
  override bundle (opencode.json fragments + env + sampling) materialized into the
  checkout per run. Model + engine fixed throughout. Each config = one full run of
  the 8-instance subset, stack restarted between configs for fresh memory.

### Per-lever results (frozen subset `b8733c486557`, 8 sympy instances)

`edited` = instances where the model produced a patch · `wrong` = patch made but
tests failed · `no-edit` / `timeout` / `oom` = failure modes · `wall` = total
episode wall-clock for the config.

| config | rank | pass | edited | wrong | no-edit | timeout | oom | wall |
|---|---|---|---|---|---|---|---|---|
| **baseline** (item-8 default) | — | **0/8** | 2 | 2 | 0 | 3 | 3 | 143 m |
| **minimal-toolset** (L1) | 1 | **0/8** | 0 | 0 | 5 | 0 | 3 | 43 m |
| **low-temp** — temperature 0.0 (L2) | 2 | **0/8** | 3 | 3 | 1 | 1 | 3 | 95 m |
| **terse-prompt** (L3) | 3 | **0/8** | 3 | 3 | 1 | 0 | 4 | 63 m |
| **prune-context** — `compaction.prune` (L4) | 4 | **0/8** | 4 | 4 | 0 | 2 | 2 | 120 m |

What the numbers say (the real signal, since pass rate is floored):

- **No lever passes any instance.** When the model *does* edit, it produces a
  plausible-but-wrong fix (e.g. `decompose` → `sorted(...)` with the wrong
  ordering); when it doesn't, it times out, OOMs, or gives up. Pass/fail is
  gated by the model's reasoning + the 16 GB memory envelope — both fixed here.
- **L4 (prune) and L2/L3 raised *engagement*** — 4, 3, 3 instances produced a
  patch respectively vs baseline's 2 — but never a *correct* one. Promising
  direction, no payoff on this stack.
- **L1 (minimal toolset) regressed engagement**, contradicting its research
  rank-1: 5/8 no-edit (the weak model often made a single `glob` call and stopped
  rather than driving an edit loop). The "a bad/extra tool is worse than none"
  result from frontier-model studies did **not** transfer to this small model on
  this subset — a documented surprise.
- **OOM/timeout is pervasive** on the larger instances under every config,
  reconfirming the item-9 finding that the model+engine memory/throughput
  envelope — not the harness — is the binding constraint.

### Decision (item 11)

**Adopt-if-better → nothing adopted; keep the item-8 default harness.** No single
lever beat the 0/8 baseline, so `scripts/mlx.sh`, the opencode config, and
`.opencode/` are unchanged. The deferred **adoption-file-path** sub-decision
(where a winning config would be committed) stays **unresolved by design** — it
was only to be settled if a lever won. The richer takeaway for future work: the
levers that *increase engagement* (context pruning, low temperature, a terse
directive prompt) are the ones worth revisiting **if** the model/engine envelope
is ever relaxed (a bigger or faster local model — items 9/10's domain), since on
this fixed stack the harness is not the bottleneck. The throughput tools
(`mlx_bench.py`) and this correctness tool (`harness_eval.py`) are complementary
and both retained. Full evidence in the JSONL ledger and
`docs/harness-engineering-research.md`; reproduction steps below.

**Repair-proxy interplay.** Unchanged — the proxy stayed **on** for every config
(it is the tool-call reliability floor the levers stand on, never a swept
variable). This item only **cross-references** the item-8 PR #1142 proxy-removal
follow-up; it does not resolve it.

### Re-running the experiment

Everything needed is tracked in the repo: the instrument (`scripts/harness_eval.py`),
the lever bundles (`scripts/harness_configs/*.json`), and the frozen subset
(`scripts/harness_eval_subset.json`). The heavy fixtures (cloned repos, venvs,
cached dataset rows) live under `~/.config/opencode-optimisations/harness-eval/` and are rebuilt by
`prepare`. Full reproduction from a clean machine:

```bash
# 0. Offline sanity check of the scoring machinery (no model needed).
scripts/harness_eval.py selftest

# 1. Bring the local stack up (mlx-lm server + repair proxy), fully offline.
make mlx-up

# 2. ONE-TIME, ONLINE: provision + gold-verify each instance's venv.
#    With no --instances it re-prepares exactly the frozen subset in the manifest.
#    --python 3.9 is REQUIRED (old sympy imports collections.Mapping, gone in 3.10).
#    Cached + offline after this.
scripts/harness_eval.py prepare --python 3.9

# 3. Score baseline + each lever over the frozen subset (offline; ~7.5 h total on a 16 GB M1).
#    The stack is auto-restarted between/within configs on OOM; a down server self-heals.
for c in baseline minimal-toolset low-temp terse-prompt prune-context; do
  scripts/harness_eval.py run --config "$c" --label "$c"
done

# 4. Regenerate the comparison table (also written to ~/.config/opencode-optimisations/harness-eval/summary.md).
scripts/harness_eval.py summary
```

Notes: each `run` appends one row to `~/.config/opencode-optimisations/harness-eval/ledger.jsonl`
(machine-readable, with per-instance reasons + the config hash) and per-run
artifacts (the model patch, opencode + test logs) land under
`~/.config/opencode-optimisations/harness-eval/runs/<label>/<instance>/`. To trial a **new** lever,
drop a `scripts/harness_configs/<name>.json` (same shape as the existing five) and
`run --config <name>`; to re-screen the subset, `prepare --instances <ids>` picks
only instances whose gold patch makes every F2P flip AND every P2P hold on a native
Python-3.9 venv (so dependency-coupled repos self-exclude).

## Latency optimizations (TODO item 12)

A sibling axis to items 9 (raw throughput) and 11 (task pass/fail): **wasted
wall-clock in the live interactive loop**, surfaced from one real Jaeger trace
(`d568ea63…`, 8m17s wall) of an opencode session on this stack. Three findings
drove the work, all rooted in **prefill** (not decode) being the wall (~0.4 tok/s
cold; decode held 2.5–5.4 tok/s):

- **session-title generation ran as a *separate* LLM call — 156.9s in parallel**
  with the first real turn, on the *full E4B model*, contending for the single
  Metal GPU. (opencode emits no `opencode.llm` span for it; only the repair proxy
  saw it.)
- the first turn cold-prefilled an **18.6 KB system prompt + 11 tool defs =
  9447 tokens** → ~103s before the first token (`cache_read=0`).
- a wholesale large-file `read` pushed one turn to **16139 tokens (80 KB)** → 252.6s.

### A — title slot → a tiny QAT model on a second port (adopted: serve)

The title call no longer runs on E4B. A **Gemma 3 270M QAT 4-bit instruct** model
(`mlx-community/gemma-3-270m-it-qat-4bit` @ `71fb198f2649a80259f9f5fe878dd9dd25638a65`,
245 MB safetensors) is served on a **second `127.0.0.1` port** (`MLX_SMALL_PORT`,
default 8082) and wired into opencode's `small_model` slot. Picked + justified in
`docs/small-model-research.md`. The research recommended *disable title-gen* as the
safe default, but opencode exposes **no documented auto-title-disable** knob, so
serving a tiny co-resident model is the practical path that keeps the auto-title.

- **Memory-budget check (measured).** The second `mlx_lm.server` worker is
  **~684 MB RSS** (a whole second Python+mlx runtime + the 270M 4-bit weights),
  274 MB on disk. Co-resident with E4B (~6.3 GB) + the main runtime that is still
  ~8 GB on the 16 GB M1 — wide margin, nowhere near the ~40–50K-token Metal-OOM
  cliff (the tiny model's own title-prompt KV is trivial; its context limit is
  pinned to 8192/512 in the provider config).
- **Capability gate (passed).** Asked for a title of a short bug-fix request, the
  270M produced `KeyError in pytest` (70 prompt → 6 completion tokens) — a usable,
  on-topic title. It is prompt-sensitive (a loose prompt yields verbose output),
  but opencode's title prompt is constrained; full tool-call reliability is **not**
  required for this slot. Spot-check titles after first real use.
- **Wiring.** `make mlx-pull` now fetches it too; `make mlx-up` serves it (+ a
  passthrough tracing proxy, below); `make mlx-opencode-config` writes the
  `mlx-small` provider + top-level `small_model`. Disable the whole slot with
  `MLX_SMALL=0` (titles fall back to E4B, reinstating the contention).

### B — slim the cold-start prefill prefix (adopted: trim toolset)

`make mlx-opencode-config` now writes a top-level `tools` map disabling **5 of the
11** tool defs from the cold prefix — chosen as useless-offline or low-value for a
small local model, leaving the edit/shell/read/search loop intact:

| tool | why cut |
|------|---------|
| `webfetch` | fully offline — can never succeed here |
| `task` | subagent orchestration is unreliable on a small local model |
| `patch` | redundant with `edit`/`write`; large schema |
| `todowrite` / `todoread` | todo bookkeeping — overhead, low value for the local loop |

Scored **only on cold-prefill token count / duration** (item 11 owns task pass/fail
for the same lever). The system prompt itself is left to opencode's default plus
the layered rules file (C) — a full per-agent `prompt` override risks dropping the
default tool-use guidance the fragile Gemma-4 tool path depends on.

### C — bound per-turn context from tool output (adopted: read-range rules)

`make mlx-opencode-config` generates `~/.config/opencode/mlx-gemma-rules.md` and
references it via opencode `instructions` (layered **on top of** the system prompt,
not a replacement). It directs the model to use `read` `offset`/`limit` ranges,
narrow with `grep` first, and never read large files whole — so a single `read`
can't blow a turn to 16k tokens again. Scored **only on worst-case per-turn prompt
size**.

### D — title/small-model calls are first-class in tracing

The repair proxy (`scripts/mlx_repair_proxy.py`) now stamps every
`mlx.chat.completions` span with `gen_ai.agent.role` (`main` or `small_model`,
from the new `MLX_PROXY_ROLE`) and a `gen_ai.agent.is_title_call` heuristic. The
small model is fronted by a **second proxy instance** (`role=small_model`, repair
**off**, OTLP service `mlx-proxy-small`) so its title call — which neither opencode
nor the main proxy would otherwise span — appears in Jaeger, grouped under the
session by the shared `sha256(sessionID)[:32]` trace id. With `MLX_SMALL=0` the
content heuristic still tags a title call that lands on the main proxy.

### Measured before/after (2026-06-21, Jaeger traces)

Headless `opencode run "Reply with exactly one word: pong"` against E4B, traced to
Jaeger, **MLX_SMALL=0 (title on E4B) vs MLX_SMALL=1 (title on the 270M)**. The
proxy's per-request `mlx.chat.completions` spans give exact per-call durations:

| span | BEFORE (title on E4B) | AFTER (title on 270M) |
|------|----------------------:|----------------------:|
| **title call** (`is_title_call`, 0 tools) | **59.26s** (role=main, E4B) | **0.07s** (role=small_model, 270M) |
| **first real turn** (5 tools) | 58.92s | **34.85s** |
| **session wall** (`opencode.session`) | 60.08s | **35.91s** |
| `/usr/bin/time` real | 61.02s | 36.86s |

**The headline win.** BEFORE, the title call (59.26s on the full E4B) ran *in
parallel* with the first real turn (58.92s) — both ~59s because they fought over
the single Metal GPU. AFTER, the title call dropped to **0.07s** on the 270M, and
with the GPU no longer split the first turn fell **58.92s → 34.85s** on its own.
Net session-start: **~60s → ~36s, a ~40% (24s) cut.** (`d568ea63…`'s 157s stall
was a heavier build session; the contention mechanism is the same.)

**B (tool cut) is active** — both turns show `tools=5` (down from 11). **D works**
— AFTER, the title call is a distinct `role=small_model` span on service
`mlx-proxy-small`, grouped with the main `role=main` span under one session trace.

**Tool-call round-trip preserved.** `opencode run` "create a file then `cat` it"
under the trimmed toolset created the file and ran the shell command correctly —
B's cut does not break the main model's edit/shell/Q&A loop (item-11 constraint).

**C (read-range rules)** is in place (advisory, layered via `instructions`); its
worst-case per-turn delta needs a large-file-read scenario and is left as an
advisory policy rather than a measured number — low-risk and reversible.

**Reproduce / revert.** Source `~/.config/opencode-optimisations/opencode-otel.env`, then trace one
session per config (compare in <http://127.0.0.1:16686>, filter service
`mlx-proxy-small` for the title call). Revert any lever: `MLX_SMALL=0` (A), or drop
the `tools`/`instructions` keys (B/C) and re-run `opencode-config`.

## Harness engineering 2 — skills, prompt diet, read caps (TODO item 13)

A sequel to items 11/12 on the same fixed stack (E4B QAT via mlx-lm). Three
coupled threads, scored on items 11/12's own axes (prefill/latency + tool-call
reliability), all shipped through the `opencode-config` generator. **Adopt-if-better.**

### Thread 1 — skills mechanism (adopted: opencode's native Skill subsystem)

opencode 1.17.7 has a **native Skill subsystem** (verified from source at
`packages/opencode/src/skill/` + `tool/skill.ts`): markdown files with
`name`/`description` frontmatter, discovered from `cfg.skills.paths` (and
`{skill,skills}/**/SKILL.md` under config dirs, plus `.claude/skills` / `.agents`).
The decisive property: a skill's **body loads only when the model calls the `skill`
tool** for it — **only the short `description` is resident** in the prompt (via the
system prompt's skills block + the `skill` tool def). That is exactly the
on-demand surface the item wanted.

| candidate | resident cost | model-selectable | verdict |
|-----------|---------------|------------------|---------|
| **native Skill** (`skills.paths` → `SKILL.md`) | resident `<skill>` block — name + description + `file://` location, **+122 tok measured** | yes (via `skill` tool) | **adopted** |
| custom command (`command/*.md`) | none until invoked | no — **user-typed only**, the model can't pull one in mid-task | rejected (not model-driven) |
| per-agent `prompt` | **fully resident** once the agent is selected, and **overrides** the built-in prompt (drops default tool guidance — item 12-B risk) | n/a | rejected (resident + risky) |
| `instructions` array | **always resident** every turn | n/a | rejected by the item (wrong place) |

Adopted skill: **`coding-discipline`** (a *separate* opencode-native coding-skill
set — read-paging, search, edit patterns, and the CLI-only invariant for
service-driving — **distinct from `.claude/skills/`**, which are the repo's
service-automation skills and are left untouched). Generated into
`~/.config/opencode/skill/coding-discipline/SKILL.md` and registered via
`skills.paths`. Confirmed live: `GET /skill` lists it; the `skill` tool is present;
the body is absent from the resident prompt until invoked.

### Thread 2 — system-prompt diet (adopted: conservative)

**Audit.** The resident prompt for the local Gemma is opencode's **`default.txt`**
(~8.5 KB / ~2100 tok), *not* `gemini.txt` — the model id is the on-disk weights
path containing `gemma-4-…`, which matches none of the `gemini-`/`claude`/etc.
branches in `session/system.ts`, so it falls through to `default.txt`. (The "18.6 KB"
of items 11/12 is `default.txt` + the env block + tool defs + skill descriptions.)
Classifying `default.txt`: tone/verbosity, proactiveness, conventions, code style,
doing-tasks, tool-usage, code-references are **always-needed**; a few lines are
**dead on this stack** (the `/help`+feedback+WebFetch-for-docs block and the "prefer
the Task tool for file search" line — `webfetch`/`task` are disabled by item 12-B).
**But `default.txt` is built-in and only overridable by a full per-agent `prompt`,
which drops the default tool guidance the fragile Gemma-4 tool path depends on
(item 12-B).** So the conservative path can't trim those dead lines without taking
the aggressive override's reliability risk.

**Decision: conservative.** Keep zero built-in override. Slim only the repo's *own*
layered `mlx-gemma-rules.md`: the **always-needed read-range discipline stays
resident** (the item's resolved constraint), and the **situational detail**
(detailed read-paging + edit patterns) moves to the on-demand `coding-discipline`
skill. **Measured live (before/after, real Gemma tokenizer):** resident-rules file
**250 → 167 tok** (−83); the skill adds **+122 tok** resident (the `<skill>` block is
name + description + a full `file://` location, not the description text alone — this
corrects the first +58-tok estimate), for a **net +39 tok** resident change — a small
**increase**, not the ~−26-tok saving first estimated. The ~1.6 KB of detailed guidance
is now **non-resident** (loaded only when needed). Threads 1+2 are thus a
**maintainability move** (situational guidance off the hot path, loadable on demand),
not a cold-start token win; the cold-start cost is +0.18s (+0.6%, within noise), kept
as the standing baseline on that basis. The unambiguous prefill win is the read cap
(thread 3, below).
A modest cut, as expected for the conservative path; the aggressive `prompt`
override (which would cut the ~2100-tok `default.txt` and its dead lines) was
**rejected** because the round-trip check (below) is the veto and the override's
risk to the tool loop isn't worth ~a few hundred tokens on a small, reversible win.

### Thread 3 — hard read cap (adopted: line cap + column cap in `read.ts`)

The cap lives in the **tracked `.opencode/tools/read.ts`**. Before, the tool only
bounded output when the model **passed `limit`**; a top-of-file read with no `limit`
was **uncapped**. Two enforcement sites had to be reconciled (the item's flag):
(a) the top-of-file path delegating to `rtk read --max-lines` (a line-cap that was
**unset**), and (b) the offset/limit **manual slice**. Both now apply the **same
effective window** (`effectiveLimit`), `--max-lines` is **always** set, and a model
`limit` larger than the cap is **clamped**.

**Cap unit — decided empirically here (the item deferred it):** a **line cap is the
shared lever** of both paths, so it is the primary unit — **but a line cap alone is
not OOM-safe.** MEASURED: rtk 0.42.4's `minimal` *and* `aggressive` levels **do not
truncate long lines** (a 1500×3000-char file passed through at **~564K tokens**),
contradicting the README's claim for this version. Worst-case tokens therefore scale
with line **width**, which neither rtk level nor a line cap bounds. So the cap is
**lines × columns**: `READ_MAX_LINES` (default **1500**) + `READ_MAX_COLUMNS`
(default **200**, matching the grep tool), with per-line truncation done in the tool
(`clampColumns`, elision marker). Real repo source has a **p99 line width of ~100
cols**, so 200 cols leaves all ordinary code untouched.

**Worst-case per-turn measurements** (line-numbered, rtk-filtered, the
`minified.txt` fixture = 1500 lines all at full width):

| config | worst-case est-tokens | vs ~40–50K OOM ceiling |
|--------|----------------------:|------------------------|
| **no cap** (old top-of-file path) | ~564K (1500×3000-char) | far over — single read can OOM |
| line cap 1500 only, cols 3000 | ~564K | unchanged — line cap alone useless here |
| line cap 1500, **cols 250** | ~49.9K | upper edge |
| **line cap 1500, cols 200 (adopted)** | **~40.5K** | clearly inside |
| dense 180-col file, adopted cap | ~35K | comfortable |
| normal source (p99 100 cols), cap | ~6–14K | untouched, far under |

The offset/limit **continuation footer is preserved** on both paths and now also
appears on a capped top-of-file read
(`(rtk: lines 1-1500 of 5001; capped at 1500 lines, use offset=1501 to continue)`),
so the model pages the rest. The cap is **belt-and-suspenders** with the resident
read-range rules (which stay), not a replacement.

### Scoring + adoption

- **Tool-call round-trip (item-11 reliability bar, stands in for `harness_eval.py`).**
  Live, offline, under the slimmed prompt + skills + read cap: (1) "create
  `hello.txt`=world then `cat` it" → file written + shell ran correctly; (2) "read
  `bigfile.txt` offset=100 limit=3, report line 101" → read tool returned the
  range, model reported line 101 verbatim. **The edit/shell/read loop survives** —
  the gate passes, so the changes are adopted.
- **Latency/prefill (item-12 Jaeger instrument) — measured before/after, adopted as
  baseline.** Sessions traced to Jaeger; the title call stays on the 270M
  (~0.0–0.05s, role=`small_model`), confirming item 12 is intact under the new config.
  **Resident system-prompt tokens 5,323 → 5,362 (+39, +0.7%); cold-start TTFT
  30.17s → 30.35s median (+0.18s, +0.6%, within noise).** So the diet+skill (threads
  1–2) is a small token **cost**, kept for maintainability, not a cold-start win. The
  read cap (thread 3) is the real win — **worst-case bound, not steady-state**: a
  single large/wide read drops **574K → ~60K tok (−90%)**, removing the unbounded-read
  OOM/blow-up tail (item 12-C left this as an advisory-only number; ~40.5K holds for
  realistic minified content, the cap's hard guarantee is a deterministic
  lines×cols ≈ 312K-char ceiling).
- **Generator wiring + idempotence (primary focus).** All three winners ship from
  `cmd_opencode_config`: the skill markdown + `skills.paths`, the slimmed resident
  rules, and the read-cap defaults (`mlx-read-cap.env`; logic stays in `read.ts`).
  **Verified byte-identical** across two consecutive `opencode-config` runs (no
  duplicate `instructions`/`skills` entries despite the append-only array, no
  duplicate `command/` files, stable read-cap default). Each surface has an `MLX_*`
  disable knob (`MLX_SKILLS=0`, `MLX_READ_CAP=0`) that **removes it with no residue**
  and restores byte-identical output when toggled back. `.opencode/` gitignore is
  unchanged (only `tools/` + `README.md` tracked; generated config stays untracked);
  the read-cap is the one tracked piece (it lives in `read.ts`).

**Reproduce / revert.** `make mlx-opencode-config` (re-run to confirm idempotence:
`diff <(…) <(…)`). Disable a surface: `MLX_SKILLS=0` / `MLX_READ_CAP=0`. Tune the
cap: `MLX_READ_MAX_LINES` / `MLX_READ_MAX_COLUMNS`, or per-session `READ_MAX_LINES`
/ `READ_MAX_COLUMNS` (read by `read.ts` directly).

## Signal-producing micro-tests (TODO item 14)

Items 11–13 left one gap: **no gradient.** `harness_eval.py` scores **0/8 for
every config** on this model (a real bug-fix is past the small model's reach), and
a metric pinned at zero cannot rank levers — item 13 had to skip it for exactly
this reason. Item 14 adds a **lower-bar, signal-producing** complement,
`scripts/harness_micro.py`, that scores the **atomic capabilities** the agentic
loop is built from — each isolated and individually winnable — so the aggregate
**fractional** pass-rate sits in the discriminating middle of the range and *can*
rank the three levers the user named (system prompt / skill / tool descriptions).
It **complements, never replaces** `harness_eval.py` (kept for end-to-end realism)
and is independent of `mlx_bench.py` (throughput only).

### Tiered micro-suite + grading rubric

A tiny **frozen synthetic** fixture tree lives in `scripts/harness_micro_fixtures/`
(`calc.py`, `config.py`, `helpers.py`, `utils.py`, plus `store.py` + `main.py` — a
pricing module with many *similar* `apply_*` functions and a caller, the navigation
target) — no SWE-bench dependency, no network, no checkout, tens of lines per file
so an episode never approaches the OOM ceiling. `micro_suite.json` declares the
tests across three tiers; the tier-1/2/3 split is preserved for ledger
comparability, but **after calibration the suite is deliberately weighted toward
read-precision + search** (see "Calibration" below for why):

- **Tier 1 — single tool-call fidelity.** Goal-style prompts that name the
  *objective*, not the tool or the params — the model must select the tool and
  derive the arguments. Includes the single-shot `grep` probe and the
  **navigation reads** (locate one of many similar `store.py` functions and read a
  *tight window* around it). Graded on the call: right tool, a `limit` present (so
  a whole-file read fails), an offset within ±1 of the function, and `limit ≤ 8`.
- **Tier 2 — two-step sequences.** `grep`→`read` chains that locate the right
  function among many similar ones and read a tight window. Graded on ordering AND
  the second call's **dependence** on the first (the `read` offset lands within ±1
  of the line `grep` reported) AND read-window tightness.
- **Tier 3 — micro-edits.** A tight-read-then-edit (read a window around the target
  function, *then* change one value). Graded on transcript AND **filesystem state**.
  (Calibration showed pure edit-outcome checks saturate on this model, so the suite
  keeps only one, gated on a tight read; the editing-correctness coverage that the
  model aces is documented under Calibration rather than padding the score.)

**Grading = per-tier binary checks aggregated into a fractional score.** Each check
is one deterministic yes/no assertion (`called`, `well_formed`, `arg_contains`,
`arg_basename_equals`, `order`, `read_offset_near_grep_line`, `file_contains`,
`file_absent_substring`, `only_changed`, …) evaluated against (a) the **structured
tool-call transcript** — captured via `opencode run --format json`, whose
`message.part.updated` → `part.type=="tool"` events carry each call's `tool` name
and `input` args — and/or (b) a filesystem diff of the fixture copy. A tier's score
is `checks_passed / checks_total`; the run headline is the **fractional aggregate**.
The design goal is a **non-degenerate** metric (off 0 and off 100) plus
**attributable** per-check failures that feed the Jaeger pass.

### Instrument shape + lever isolation

`harness_micro.py` is a **sibling** of `harness_eval.py`: it imports that module's
machinery (MLX server lifecycle / OOM-restart, `_deep_merge`, `config_hash`, and
the shared ledger/summary helpers) and adds only the tiered-grading logic, leaving
the SWE-bench path untouched. Both suites append to **one unified ledger**
(`~/.config/opencode-optimisations/harness-eval/ledger.jsonl`); `RunRow.suite` discriminates, and
`write_summary` renders **a table per suite** (item-11 rows leave the micro fields
empty and vice-versa).

Each config run is **isolated**: it clones the installed global
`~/.config/opencode` (which *is* the current default harness = `micro-baseline`)
into a temp dir (`node_modules` symlinked, everything else copied), applies the
lever's deltas, and points opencode at it via `XDG_CONFIG_HOME`. **The user's real
config is never mutated.** `--title micro` skips the slow model-generated session
title; the OTel plugin and 270M title slot are dropped for clean fast runs (re-add
OTel with `--trace` for the Jaeger pass). The grown config-bundle schema
(`scripts/harness_micro_configs/<name>.json`) drives all three named levers:

| field | lever | effect |
|---|---|---|
| `system_prompt` | 1 — system prompt | terse build-agent prompt **replacing** opencode's default (per-agent `prompt` replaces, not appends) |
| `rules` | 1 — resident rules | override / disable the `mlx-gemma-rules.md` instructions file |
| `skill` | 2 — coding skill | toggle the on-demand `coding-discipline` skill on/off + vary its body |
| `tools_variant` | 3 — tool descriptions | swap the custom `read.ts`/`grep.ts` for a variant under `scripts/harness_micro_tools/<variant>/` (richer descriptions + param surface) |
| `sampling`, `env`, `opencode_config` | — | sampling params, extra env, raw `opencode.json` deep-merge escape hatch |

First-sweep configs: `micro-baseline`, `micro-terse-prompt` (lever 1),
`micro-skills-off` (lever 2 — also the **`edit`/`write`-present regression guard**
for `[[opencode-skills-drops-edit]]`: with skills off the write tools must still
work, asserted by the `t1-edit`/`t1-write`/`t3-*` checks), and
`micro-verbose-tooldesc` (lever 3).

### Jaeger trajectory analysis (manual)

For each **failing/partial** micro-test, re-run it with `--trace` (keeps the OTel
plugin on) and open the session trace in local Jaeger (search by `session.id` or
the `sha256(sessionID)[:32]` trace id — see the tracing section). Read the
`opencode.tool.*` spans plus the proxy `mlx.chat.completions` system-prompt span
and hand-catalogue the failure against a **fixed taxonomy**, each mapped to a
candidate lever change:

| failure mode | typical lever response |
|---|---|
| malformed call (bad JSON / wrong shape) | tool-description / param-surface tweak (lever 3); terser prompt (lever 1) |
| unavailable tool called | trim/rename the exposed toolset; prompt names the real tools (lever 1) |
| parameter mis-fill (e.g. missing `offset`) | param description + worked example (lever 3); skill nudge (lever 2) |
| loop / repeated identical call | terser prompt; remove a distracting tool |
| context flood / no tool call before timeout | read-cap / prompt diet; investigate prefill |

The loop is **trace → taxonomy entry → lever change → re-score on the signal
suite**.

### Baseline + how to run

```bash
make harness-micro-selftest                       # offline parse/grade sanity (no model)
make mlx-up                                        # stack up (offline)
make harness-micro CONFIG=micro-baseline           # establish the baseline gradient
make harness-micro CONFIG=micro-terse-prompt       # one lever at a time …
make harness-micro CONFIG=micro-skills-off
make harness-micro CONFIG=micro-verbose-tooldesc
make harness-micro-summary                          # unified comparison table
# Jaeger pass on a failing test:
make mlx-jaeger-up
uv run python scripts/harness_micro.py run --config micro-baseline --tests t1-grep --trace
```

**Calibration to a ~50% baseline (2026-06-21/22).** The headline check is that the
metric is **non-degenerate** so it can rank levers. The suite was calibrated over
five baseline runs to land in the discriminating middle, because the first cut sat
far too high:

| suite version | baseline | why |
|---|---|---|
| v1 — outcome-only atomic tasks | **0.93** | the model nails "did the call work" |
| v2 — de-scaffolded prompts + precision checks | 0.89 | barely moved — tool selection is solved too |
| v3 — navigation + 4-site cross-file renames + compounding | 0.86 | **tier-3 = 24/24**: the model *aces* multi-step editing |
| v4 — focus on read-precision + search (drop saturated edit checks) | 0.65 | the real signal is read-window discipline + grep |
| **v5 — tightened thresholds (`limit ≤ 8`, offset ±1)** | **0.53** | **on target** |

The decisive finding: **Gemma-4B QAT is genuinely competent at agentic editing on
this model** — isolated micro-edits, two coordinated edits, even a 4-call cross-file
rename (def + internal caller + import + call site) and a no-op restraint trap all
pass. Outcome-only edit checks therefore just pin the aggregate near 0.9 and don't
*differentiate* configs. The non-saturated, **lever-sensitive** signal lives in two
places, and v5 is built almost entirely from them: (1) **single-shot `grep`
reproducibly times out** at the per-test cap with 0 tool calls (yet `grep` works
inside a `grep`→`read` chain — a specific failure mode + the first Jaeger
trajectory entry); and (2) **read-window discipline** — when asked to read just the
right function, the model often reads a wider window than a tight one (`limit ≤ 8`)
or mis-targets the offset. Both are exactly what the resident rules / coding skill /
read+grep tool descriptions target (items 12–13), so the levers have real room to
move the score in both directions. `micro-baseline` = **0.53 (18/34) — tier1 7/13 ·
tier2 8/17 · tier3 3/4**. Determinism (of the grading function), offline operation
(`HF_HUB_OFFLINE=1`, committed fixtures), and the OOM→restart path are covered by
`harness_micro.py selftest` + the shared `harness_eval` machinery.

**Adoption gate — adopt-if-better on the fractional metric**, with the item-11
tool-call round-trip veto AND the `edit`/`write`-present regression guard still
binding. A lever that lifts the micro-score but breaks the real edit/shell loop is
rejected. Because v5 is read-focused, the edit-regression guard rests on the single
`t3-tightread-then-edit` test (a total edit-drop would fail it) plus the
config-level toolset assertion in `micro-skills-off` (the `[[opencode-skills-drops-edit]]`
guard). Winners ship through `scripts/mlx.sh opencode-config` (idempotent, `MLX_*`
disable knobs). "Nothing adopted" is a valid terminal state.

**First lever sweep (2026-06-22, vs the 0.53 baseline).** The calibrated metric
discriminated — two levers moved it, via *different* mechanisms:

| config | tier1 | tier2 | tier3 | score | Δ |
|---|---|---|---|---|---|
| micro-baseline | 7/13 | 8/17 | 3/4 | 0.53 | (base) |
| micro-terse-prompt | 8/13 | 7/17 | 3/4 | 0.53 | +0.00 |
| micro-skills-off | 7/13 | 10/17 | 4/4 | 0.62 | **+0.09** |
| micro-verbose-tooldesc | 11/13 | 7/17 | 3/4 | 0.62 | **+0.09** |

- **verbose-tooldesc** (+0.09) lifts **tier-1 7→11**: the richer `read`/`grep` param
  descriptions (explicit "offset=X, limit=Y, tight window", worked examples) improve
  single-call param precision — a clean, mechanistically credible win.
- **skills-off** (+0.09) lifts **tier-2/3** (chains 8→10, edit 3→4): the on-demand
  coding-discipline skill appears to add noise rather than help tool-call fidelity.
  Note this would *reverse* the item-13 decision to keep the skill (which was kept
  for maintainability, never a measured win). The **`edit`/`write` regression guard
  holds** — editing still works with skills off, so this is not the
  `[[opencode-skills-drops-edit]]` failure.
- **terse-prompt** is flat (+0.00) — reject.

**Caveat: these are single runs** of a sampling-stochastic episode; the deltas are
+3 checks each. Before shipping either winner via `scripts/mlx.sh opencode-config`,
**confirm with a repeat pass** (and test the *combination* verbose-tooldesc +
skills-off, since the two help non-overlapping tiers and may stack toward ~0.7).
That confirmation + the generator wiring is the remaining open step.

## Online harness-soundness control (TODO item 22)

Item 16's local baseline is **0/8** with the frozen Gemma-4-E4B. That number is only
interpretable as *capability-bound* once we've proven the **harness itself** isn't
silently broken. Item 22 adds the missing control arm: run the **exact same**
full harness (same SWE subset, tools, scaffolding, scoring) against a **strong
online model** — `opencode/big-pickle`, the free hosted model on the opencode zen
gateway — and read its failure histogram.

This is a **diagnostic / CI control only — NOT a serve-path change.** The frozen
local stack (Gemma-4-E4B / mlx-lm 0.31.3, fully-local-at-serve) is unchanged; the
online model exists solely to validate the scaffolding and is never shipped or used
at serve time. It is the one explicit **online** exception — run on demand, never in
the offline serve path.

### The `external_provider` gate

`scripts/harness_eval.py` wires the local stack in at three coupled points, all of
which the `external_provider` flag short-circuits so the run works with **MLX fully
off**:

1. **`apply_levers`** — normally writes the `mlx-local` custom provider block
   (`npm` + `options.baseURL` → the local `/v1` endpoint). With the gate on it writes
   **no `mlx-local` block and no local `baseURL`**; instead it pins `model`/`small_model`
   to the external ref and attaches the served model's `options`/`limit` under
   opencode's **own built-in provider** (e.g. `provider.opencode.models.big-pickle`),
   so opencode resolves the ref through the gateway while provider-appropriate
   sampling/context limits still flow.
2. **`cmd_run`** — skips `server_healthy`/`restart_server` (there is no local endpoint
   to health-check or bounce).
3. **`detect_model` + the OOM path** — skips `detect_model(/v1/models)` (takes the ref
   straight from config/`--model`) and the per-instance OOM-vs-timeout probe + the
   `_score_subset` OOM-restart (which would otherwise mislabel every online timeout
   as `oom`).

In place of the removed MLX health-check, an **auth/connectivity pre-flight**
(`online_preflight`) runs once before the subset loop: it confirms `opencode` is on
`PATH` and pings the model ref with a trivial `opencode run`, aborting early with a
`run 'opencode auth login' + check network` remediation instead of letting all 8
instances fail opaquely. `selftest` asserts the gate: with `external_provider` on the
written `opencode.json` has no `mlx-local`/`baseURL` and pins the external ref.

### Running it (one command, online)

```bash
# One-time (if the free gateway ever requires it): opencode auth login → `opencode`
make harness-eval-online                      # CONFIG defaults to online-bigpickle
# or, explicitly / for a variant config:
make harness-eval-online CONFIG=online-bigpickle HARNESS_ARGS="--repeats 3"
```

No `make mlx-up` — the run needs **network** (and, if the gateway requires it, a
one-time `opencode auth login`) but no local stack. The `online-bigpickle.json`
lever config carries `external_provider` + `model_ref` + the deltas; its
`description` is the in-ledger record of what is held vs. varied.

**Identical where it proves soundness, provider-appropriate elsewhere.** Tools,
prompt, subset, and scoring are held byte-identical to the Gemma arm. The recorded
deltas: greedy sampling (`temperature=0.0`) and a shorter per-instance
`timeout=240s` (the default 600s is tuned for ~8-12 tok/s Gemma and would
over-generously cap a fast gateway model). Every delta is in the config `description`
→ ledger `notes`, so the control is auditable.

### Verdict (banded; histogram is primary)

On the 8-instance tier≥3 subset:

- **≤1/8 ⇒ harness broken** (same dead-zone as Gemma) — a mechanical bug; fix the
  harness before trusting any item-16 lever signal.
- **≥5/8 ⇒ harness sound** — the local 0/8 is genuinely capability-bound; item-16's
  framing holds.
- **2–4/8 ⇒ inconclusive** — opens item 22.5 (read one passing + one failing trace,
  re-run the borderline instances at higher K to separate a real bug from gateway
  run-to-run nondeterminism).

**The histogram is the primary evidence, pass-rate secondary.** The
`failure_category` taxonomy is provider-agnostic (derived from terminal `reason` +
E0 metrics, never model identity), so BigPickle drops into the same 10-category
vocabulary with zero code change. The "harness sound" signature is BigPickle landing
mostly in **`ok`/`tests-failed`** (capability modes) with **ZERO
`oom`/`degenerate-loop`/`no-edit`/`edit-mismatch`** (mechanical/harness modes).

### Verdict (2026-06-24): HARNESS SOUND

BigPickle scored **4/8** on the identical subset. The aggregate lands in the numeric
*inconclusive* band, but the pre-registered 22.5 disambiguation (re-run the failures
at the Gemma-identical 600s cap + read traces) resolves it on the histogram:

| arm (same subset `b8733c486557`) | pass | failure histogram |
|---|---|---|
| Gemma-4-E4B baseline (K=3 mean) | **0/8** | `tests-failed`, `timeout`, `no-edit` — never one `ok` |
| BigPickle @240s (22.3) | **4/8** | `ok`×4, `timeout`×2, `catastrophic-edit`×1, `no-edit`×1 |
| BigPickle @600s (22.5, Gemma-identical) | **4/8** | `ok`×4, `tests-failed`×3, `catastrophic-edit`×1 |

At the Gemma-identical 600s cap the failure modes are **100% capability modes**
(`tests-failed`/`catastrophic-edit`) with **ZERO mechanical/harness modes** (no `oom`,
`degenerate-loop`, `no-edit`, or `edit-mismatch`) — the exact "harness sound"
signature. Trace reading confirmed the pipeline: a PASS (sympy-15345) captured a real
`_print_Max/_print_Min` fix → 10 tests passed; the 22.3 `no-edit` (sympy-19007) was a
genuine output-budget (`length`) cutoff with zero edit attempts, not a harness miss —
at 600s it completes with a real edit and **F2P 1/3 partial**, proving the scorer reads
actual pytest results. The 22.3 `timeout`/`no-edit` rows were artifacts of the
deliberately-tightened 240s cap; at 600s they vanish.

**Decisive contrast:** Gemma never writes a single correct fix (0 `ok` across 3
repeats) while BigPickle writes 4 on the **identical** scaffolding — so the harness
demonstrably *can* score passes, and the local 0/8 is genuinely **capability-bound**,
not harness-broken. Item-16's framing holds; the GEPA/prompt work it gates is
unblocked. One-shot control — re-run only after structural harness changes.

## Improvement-recommender (TODO item 18)

Item 16 proved that **trace-review by hand** finds the real defects (L3a
patch-capture, L6 thinking-stop, L3b edit-matcher, L5 loop). Item 18 **automates that
diagnostic loop**: a deterministic Python digest of the on-disk corpus feeds an
Opus-4.8 reasoner that emits ranked, evidence-backed lever proposals. It is
**analysis over artifacts already on disk** — not a new serve-path lever — so unlike
items 19/20 it is *not gated* behind item-16's pass-rate moving.

**Input corpus = the durable local jsonl, NOT Jaeger.** Every episode already
persists its full `--format json` NDJSON to `runs/<run>/<instance>/opencode.jsonl`,
and `ledger.jsonl` + `tier-report.jsonl` carry the per-instance E0 metrics and the
per-tier × failure-mode histogram. The recommender reads those (`parse_episode_jsonl`
is reused verbatim). Jaeger/OTel is the *live human debugging* aid (see
[`jaeger-tracing.md`](jaeger-tracing.md)); it is in-memory/ephemeral and carries no
per-token text, so it is the wrong source here.

### Two layers

- **Layer 1 — evidence digest (deterministic, Python, unit-tested).** The
  `harness_eval.py recommend` subcommand aggregates the ledger corpus by
  `failure_category × tier`: per mode a count, the distinct instance IDs, the tiers it
  hits, and an **E0 metric signature** (mean steps / steps-to-first-edit / output
  tokens / tool-call rounds; `made_edit_rate`, `degenerate_loop_rate`,
  `dropped_output_rate`, `timed_out_rate`, `common_errored_tools`); per tier the
  pass-rate + headroom + a `movable` flag. `ranked_cells` orders `(mode, tier)` cells
  by `count × headroom × movable`, so the stable-0/8 **T3/T4 capability wall**
  (`movable: false`) is reported but **zeroed** in the priority hint and only the
  climbable T1/T2 rungs surface. Layer 1 reuses `classify_failure` / `instance_tier`
  unchanged — it speaks item-17's vocabulary with no new enum — and crucially **does
  not rank or invent levers**; it produces the grounded evidence the proposer reasons
  over. It is offline, under `make check`, and selftested.
- **Layer 2 — proposer (Claude Code, Opus 4.8).** A Claude Code agent on Opus 4.8
  consumes the Layer-1 digest + the prior-work docs (`docs/*-research.md`, `TODO.md`,
  `CHANGELOG.md`) and emits **ranked recommendations**, each tying a failure mode to
  evidence (instance IDs + metric deltas) and a proposed lever. A lever expressible in
  the existing config schema (`sampling` / `opencode_config` / `env` / `system_prompt`
  / `external_provider` / `model_ref` / `timeout`) is materialised as a runnable
  `harness_configs/*.json`; a defect needing **new code** (an `.opencode/tools/*.ts`
  shadow, a proxy change) is emitted as a flagged **`needs-implementation` note** (mode
  + evidence + `target_seam`), never a runnable config. The proposer prompt lives at
  `scripts/recommender_proposer_prompt.md`; a worked output at
  `scripts/recommender_sample_proposal.json`.

### Two gates keep the LLM honest

The deterministic Layer 1 carries both gates, so a non-deterministic proposer can't
silently get something wrong A/B'd:

- **Schema validation (18.2)** — `recommend --validate PROPOSAL.json` checks every
  emitted config against the lever schema (a non-schema key ⇒ `needs-implementation`,
  not a runnable config) and that each `needs-implementation` names a `target_seam`. A
  malformed proposal is rejected, not run.
- **Known-answer backtest (18.0)** — `recommend --backtest PROPOSAL.json …` scores the
  proposer's `(failure_mode, instance)` claims for **recall AND precision** against the
  labelled item-16 ground truth (`RECOMMENDER_GROUND_TRUTH`): dropped-output/thinking-
  stop → `no-edit` on 12481/11400/19007; edit gutter/whitespace → `edit-mismatch` on
  15345/13043; the 364-round loop → `degenerate-loop` on 19007. Because the proposer is
  an LLM, the bar must hold on the **majority** of several samples (mirrors item-16's
  K-run discipline) — a recommender that flags everything fails on precision.

**Validation result (2026-06-25).** Over the baseline pre-fix digest, three Opus-4.8
proposer samples each scored **recall = 1.0, precision = 1.0** (3/3 clear the bar) —
they rediscovered all three known item-16 defects on their exact instances with zero
over-flagging. The recommender itself is therefore **certified**.

A **rerun once item-16's L0–L6 lever sweep was merged in** (its full adopt/reject
verdicts now in the proposer's prior-work context) is the more telling result, and is
the committed golden sample (`scripts/recommender_sample_proposal.json`): with the
cheap schema-expressible levers *already swept and rejected* (L1 anti-repetition, L3/L6
prompt, L5 `doom_loop`, low-temp — none moved SWE 0→>0), all three samples routed
**every** defect to a `needs-implementation` note pointing at the genuinely un-tried
structural seams — repair-proxy **no-text-stop recovery** (`mlx_repair_proxy.py`) for
the dropped-output `no-edit` mode, a **whitespace-tolerant edit matcher**
(`.opencode/tools/edit.ts`) for `edit-mismatch`, and a **churn-aware loop guard**
(explicitly *not* opencode's rejected identical-call `doom_loop`) for the 19007
`degenerate-loop` — each rationale honestly citing the prior reject verdict. The
substantive conclusion: **there is no new schema-expressible lever worth A/B-ing; the
remaining work is structural.**

The decisive **18.3** step (A/B an emitted runnable config at K≥3 via
`harness_eval.py run`) re-runs the *local* Gemma stack and needs the MLX server up.

**18.3 result — verdict REJECT (2026-06-26).** Ran the first-pass
`proposed-greedy-toolprotocol` config (greedy temp 0.0 + a terse small-model tool-use
protocol *replacing* the long frontier-tuned system prompt; targets the
`no-edit`/dropped-output mode) at K=3 (`label item18-ab-greedytool`) on the identical
frozen 8-instance subset (`b8733c486557`, 600 s cap), vs the `baseline-tier-r1..r3` K=3
arm. **Pass-rate 0/8 → 0/8** (null, spread 0 — the L0–L6-aware rerun's predicted
tripwire-on-the-capability-wall null held). **But the histogram regressed in the wrong
direction and tool-call validity broke**, over K=3 (24 episodes each):

| metric | baseline (default prompt) | proposed (greedy + terse protocol) |
| --- | --- | --- |
| `no-edit` | 5 | **18** |
| `made_edit` | 16/24 | **2/24** |
| `tests-failed` | 12 | **1** |
| tool-calls (sum) | 167 | **34** |
| `dropped_output` | 2 | **9** |

**Replacing** the long tuned system prompt with a terse 4-sentence protocol *suppressed*
tool use on the weak 4B — it narrates the fix instead of emitting the edit tool-call. The
18.3 bar (move a tier pass-rate **or** shift the histogram favourably, **with tool-call
validity not regressed**) fails on the disqualifying clause → **the config is rejected.**
This refines item 19: *additive* terse `rules.content` helps (T2 0.733→0.917), but
*gutting* the system prompt for a terse one **hurts** — the long tuned prompt is
load-bearing tool-use scaffolding. The recommender PIPELINE is validated (18.0 backtest
3/3); its first emitted lever, like every item-16 mechanical lever, does not move the
capability wall, and a wrong-direction prompt swap actively regresses the floor.

### Commands

```bash
# Layer 1 — evidence digest over the on-disk corpus (offline; writes recommend-digest.json)
python scripts/harness_eval.py recommend                  # whole corpus
python scripts/harness_eval.py recommend --config baseline --suite swebench

# Gate a proposer output (Layer 2 → these):
python scripts/harness_eval.py recommend --validate scripts/recommender_sample_proposal.json
python scripts/harness_eval.py recommend --backtest sample1.json sample2.json sample3.json

# 18.3 close-the-loop (NEEDS the local MLX stack up — make mlx-up):
python scripts/harness_eval.py run --config proposed-greedy-toolprotocol --repeats 3
```

## Structured prompt-optimisation — GEPA (TODO item 19)

**Closed 2026-06-26 — verdict ADOPT (modest local win).** With item-16's gate
satisfied (harness sound, 0/8 capability-bound), a reflective optimiser was applied to
the harness **text levers** (the `mlx-gemma-rules.md` content), with **Opus 4.8 as the
in-loop reflector** (item-18 pattern) and the **frozen local Gemma as optimisee +
evaluator** — serving stays offline throughout (`gepa_assert_serving_offline` rejects any
candidate that would move the evaluated model off the local stack).

**19.2 feasibility gate (UNLOCKED).** A deterministic fitness/gate core
(`gepa_fitness` = `T2_frac − λ·floor_rise`, λ=100, T1 hard gate; `gepa_gate_check`;
cheap pure-ledger reads; `gepa_budget`) decides whether GEPA may run. A fresh **K=5**
baseline re-measure gave **T2 mean 0.733, spread 0.167**, so headroom 0.267 > spread →
**gate UNLOCKED** (a real, above-noise climbable signal on the synthetic T2 rung).
Per-rollout ≈78.5 s; per-candidate ≈23.6 min (K=3). `make gepa-gate`.

**19.3 GEPA run (ADOPT cand2).** The sole failing T2 check was
`read_offset_near_grep_line` — the model greps correctly but reads >1 line *above* the
matched line, even though the default rules already state the rule in prose.
- **cand2** (terse, positive-only rules, 233 ch) → **T2 0.733→0.917** (K=6, Δ+0.183 >
  spread; floor 1.6→0.5; T1 held). **ADOPTED** (`scripts/harness_micro_configs/gepa-cand2.json`).
- **cand1** (verbose, +WRONG/RIGHT numeric example, 1025 ch) → **REGRESSED to 0.278**
  (the counter-arm).
- **Decisive finding:** on this weak 4B model **prompt LENGTH is the dominant lever** —
  terseness helps, elaboration hurts. This *refines* item-16's "prompt changes don't
  move this harness": they do, but only in the less-is-more direction. The search
  converged in 2 candidates (no CAPO/OPRO fallback). Offline re-validation confirmed the
  win survives (online K3=1.0, re-val K3=0.833, combined K6=0.917).
- **Caveat:** the win is on the synthetic T2 tool-fidelity rung; **T3/T4 stay 0/8** (the
  capability wall — unmovable by a prompt lever). Full write-up:
  `docs/structured-optimisation-research.md` §19.2–19.3.

```bash
# (gate) decide whether GEPA may run — offline, reads the ledger
python scripts/harness_eval.py gepa-gate
# evaluate a candidate (NEEDS the local MLX stack — make mlx-up), then score it:
make harness-micro CONFIG=gepa-cand2 MICRO_ARGS="--label gepa-cand2-r1"
python scripts/harness_eval.py gepa-score --cand-prefix gepa-cand2-
```

## Troubleshooting

- **OOM / memory pressure on 16 GB** — stick to E4B (or drop to E2B). Close
  other heavy apps; MLX uses unified memory, so the model competes with
  everything else. The 12B QAT is not recommended on 16 GB.
- **`weights … not found` at start** — run `make mlx-pull` first. The server
  never downloads (it runs with `HF_HUB_OFFLINE=1`).
- **`server did not come up` with a PyPI / DNS error in the log** — `uvx`
  resolves `mlx-lm` against PyPI by default, so an offline `make mlx-up` fails
  before the server starts. `mlx-up`/`mlx-serve` now run `uvx --offline` and
  `make mlx-pull` warms uv's tool cache, so this is handled — but if you bumped
  `MLX_LM_VERSION` without re-running `make mlx-pull`, the new version isn't
  cached yet. Re-run `make mlx-pull` once while online (it warms the cache), or
  do a one-off `MLX_UVX_OFFLINE=0 make mlx-up` while online.
- **opencode can't reach the model** — confirm `make mlx-status` shows
  *running* and the `baseURL` is exactly `http://127.0.0.1:8080/v1` (with `/v1`).
- **Slow first token** — the model loads into memory on the first request after
  start; subsequent requests are faster.

## Model-upgrade procedure

To change the model or revision, edit the pins at the top of `scripts/mlx.sh`
(`DEFAULT_MODEL`, `DEFAULT_REVISION`, `MLX_LM_VERSION`) and mirror them in the
**Pinned model** table above, then re-run `make mlx-pull`, restart with
`make mlx-down && make mlx-up`, re-run `make mlx-opencode-config`, and update
the tool-call reliability notes before relying on the new pin.
