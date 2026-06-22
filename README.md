# opencode-optimisations

A **zero-cost, fully-local** coding-agent stack: [opencode](https://github.com/sst/opencode)
(an open-source terminal coding agent) driving a local **Gemma 4 QAT** model
served on-device through Apple's **MLX** stack. No code or prompt leaves the
machine. Around that core sit the levers that make a small, slow local model
usable for real coding work — and the instruments that measure whether each
lever actually helps.

This repository was lifted out of a larger personal automation toolkit, where
it grew incrementally as a "local coding agent" side-project. It now stands on
its own.

## What's here

| Piece | Path | Purpose |
|---|---|---|
| **Serving controller** | `scripts/mlx.sh` | `pull` / `up` / `down` / `status` / `serve` / `opencode-config` / `jaeger-up` / `jaeger-down` — manages the MLX server, a small title-model, the repair proxy, and Jaeger |
| **Tool-call repair proxy** | `scripts/mlx_repair_proxy.py` | stdlib proxy in front of the model that repairs mlx-lm Gemma 4 tool-call parser bugs and emits OTLP system-prompt spans |
| **Jaeger / OTel integration** | `scripts/patch_otel_plugin.py` | idempotent patcher that flushes spans on end and groups a session into one deterministic trace |
| **Token-reduction opencode tools** | `.opencode/tools/read.ts`, `.opencode/tools/grep.ts` | custom `read`/`grep` that shrink tool output before it reaches the model |
| **Throughput benchmark** | `scripts/mlx_bench.py` | multi-turn agentic TTFT / tokens-per-second of the local server |
| **Pass/fail harness** | `scripts/harness_eval.py` | scores opencode task pass/fail on a frozen SWE-bench Lite subset (model + engine fixed; tunes opencode-side levers) |
| **Tool-call-fidelity micro-suite** | `scripts/harness_micro.py` | lower-bar, gradient synthetic suite whose fractional pass-rate can rank prompt / skill / tool-description levers |

Full methodology and results live in [`docs/opencode-local.md`](docs/opencode-local.md)
(the master doc), with config best-practices in
[`docs/opencode-config.md`](docs/opencode-config.md), the complete tracing setup
in [`docs/jaeger-tracing.md`](docs/jaeger-tracing.md), and the background research
in [`docs/local-model-throughput-research.md`](docs/local-model-throughput-research.md),
[`docs/local-inference-engines-research.md`](docs/local-inference-engines-research.md),
[`docs/small-model-research.md`](docs/small-model-research.md), and
[`docs/harness-engineering-research.md`](docs/harness-engineering-research.md).

## Prerequisites

- **Apple Silicon** Mac (the MLX serving path is Metal-only).
- **[`uv`](https://docs.astral.sh/uv/)** — manages this repo's venv and runs the
  Python instruments. `mlx-lm` itself is launched isolated via `uvx` (never
  imported), so it never perturbs this repo's environment.
- **[opencode](https://github.com/sst/opencode)** — the terminal coding agent.
- **Docker** (or a local Jaeger binary) — for the optional tracing backend.
- **[Bun](https://bun.sh)** (or npm) — to install the `.opencode/` custom tools'
  one dependency (`@opencode-ai/plugin`). See the quick-start step below.
- **[ripgrep](https://github.com/BurntSushi/ripgrep)** (`rg`) — the custom
  `grep.ts` tool shells out to a real `rg` binary.
- **rtk** (Rust Token Killer) — the custom `read.ts` tool filters file contents
  through `rtk read` to cut tokens. If `rtk` is not on `PATH`, the MLX serving
  loop still works; only the token-reduction `read` tool degrades. See
  [`.opencode/README.md`](.opencode/README.md) for what the tools expect.

## Quick start

```bash
# 1. Python env + dev tooling
uv sync

# 2. Install the .opencode custom-tool dependency (once)
cd .opencode && bun install   # or: npm install
cd ..

# 3. One-time model weight download (offline-serving thereafter)
make mlx-pull

# 4. Spin the local server up on 127.0.0.1 (also brings up tracing)
make mlx-up

# 5. Point opencode at the local provider
make mlx-opencode-config

# ...code with opencode against the local model...

make mlx-status     # is it running, on what port / model
make mlx-down       # spin everything down
```

Measurement instruments (all need the stack up; all record to JSONL ledgers
under `~/.config/opencode-optimisations/`):

```bash
make mlx-bench LABEL="E4B baseline"        # throughput
make harness-eval-prepare                   # one-time, ONLINE: build SWE-bench venvs
make harness-eval CONFIG=baseline           # pass/fail over the frozen subset
make harness-micro CONFIG=micro-baseline    # tool-call fidelity
make harness-micro-summary                  # unified comparison table
```

## Development

```bash
make check    # ruff + mypy + pytest
```

## License

MIT — see [LICENSE](LICENSE).
