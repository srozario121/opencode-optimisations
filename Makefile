# Canonical dev-command layer for opencode-optimisations. Targets wrap uv +
# scripts/mlx.sh so the README and the docs share one vocabulary.

.PHONY: sync lint typecheck test check \
        mlx-pull mlx-up mlx-down mlx-status mlx-serve mlx-opencode-config \
        mlx-jaeger-up mlx-jaeger-down mlx-bench mlx-bench-summary \
        harness-eval harness-eval-prepare harness-eval-summary harness-eval-online \
        harness-recommend \
        harness-micro harness-micro-selftest harness-micro-summary

# --- quality gate (mirrors the originating repo's tooling) ---
sync:
	uv sync

lint:
	uv run ruff check scripts

typecheck:
	uv run mypy scripts

# Exit 5 = "no tests collected"; the instruments carry their own `selftest`
# subcommands today, so an empty pytest run is a pass, not a failure.
test:
	uv run pytest || [ $$? -eq 5 ]

check: lint typecheck test

# --- local-model coding agent (opencode + Gemma 4 QAT via MLX) ---
# One-time weight download, then spin the OpenAI-compatible server up/down on
# 127.0.0.1. See docs/opencode-local.md.
mlx-pull:
	scripts/mlx.sh pull

mlx-up:
	scripts/mlx.sh up

mlx-down:
	scripts/mlx.sh down

mlx-status:
	scripts/mlx.sh status

# Foreground server (Ctrl-C to stop) — handy for debugging.
mlx-serve:
	scripts/mlx.sh serve

# Install the opencode provider config pointing at the local server.
mlx-opencode-config:
	scripts/mlx.sh opencode-config

# Jaeger tracing backend on its own (the tracing half of `mlx-up`).
mlx-jaeger-up:
	scripts/mlx.sh jaeger-up

mlx-jaeger-down:
	scripts/mlx.sh jaeger-down

# --- throughput benchmark ---
# Drives the running OpenAI-compatible 127.0.0.1 endpoint; headline metric is
# multi-turn agentic TTFT. Pass a label: make mlx-bench LABEL="E4B baseline".
# Extra args via BENCH_ARGS. See docs/opencode-local.md.
mlx-bench:
	uv run python scripts/mlx_bench.py --label "$(LABEL)" $(BENCH_ARGS)

mlx-bench-summary:
	uv run python scripts/mlx_bench.py --summary

# --- harness-engineering eval (SWE-bench Lite pass/fail) ---
#   make harness-eval-prepare              # one-time, ONLINE: build+verify venvs (py3.9)
#   make harness-eval CONFIG=baseline      # score one lever config over the subset
#   make harness-eval-summary              # print the comparison table
# Extra args via HARNESS_ARGS. Needs the local stack up (make mlx-up) EXCEPT for
# harness-eval-online (item 22), which is the one online exception — it runs the
# control arm through opencode's own provider with MLX OFF (no mlx-up).
harness-eval-prepare:
	uv run python scripts/harness_eval.py prepare --python 3.9 $(HARNESS_ARGS)

harness-eval:
	uv run python scripts/harness_eval.py run --config "$(CONFIG)" $(HARNESS_ARGS)

# Item 22 online harness-soundness control. NO mlx-up dependency. Requires
# network + a one-time `opencode auth login` to the `opencode` provider. The
# online-bigpickle config carries external_provider + model_ref + timeout, so the
# default CONFIG runs the control in one command; override CONFIG for variants.
harness-eval-online:
	uv run python scripts/harness_eval.py run --config "$(or $(CONFIG),online-bigpickle)" $(HARNESS_ARGS)

harness-eval-summary:
	uv run python scripts/harness_eval.py summary

# Item 18 Layer-1 evidence digest over the on-disk episode/ledger corpus (offline,
# no model / no mlx-up). Override the scope with RECOMMEND_ARGS, e.g.
#   make harness-recommend RECOMMEND_ARGS="--config baseline --suite swebench"
#   make harness-recommend RECOMMEND_ARGS="--validate scripts/recommender_sample_proposal.json"
harness-recommend:
	uv run python scripts/harness_eval.py recommend $(RECOMMEND_ARGS)

# --- signal-producing micro-test harness (tool-call fidelity) ---
#   make harness-micro CONFIG=micro-baseline      # score one lever config
#   make harness-micro-selftest                   # offline parse/grade sanity (no model)
#   make harness-micro-summary                    # print the unified comparison table
# Extra args via MICRO_ARGS. Shares the unified ledger with harness-eval.
harness-micro:
	uv run python scripts/harness_micro.py run --config "$(CONFIG)" $(MICRO_ARGS)

harness-micro-selftest:
	uv run python scripts/harness_micro.py selftest

harness-micro-summary:
	uv run python scripts/harness_micro.py summary
