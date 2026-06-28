# Item 24.2 — per-candidate SERVE RECIPES (small-model survey)

The local model is chosen at the **serve layer** (`MLX_MODEL`/`MLX_REVISION` → `scripts/mlx.sh`),
not in the harness config JSON. `harness_eval.py` auto-detects whatever model is live at the
local `/v1` endpoint (`detect_model(base_url)`), so each `model-<name>.json` config carries
**sampling/rules only**. This file is the env + pull + revision-pin recipe per candidate, plus
the proxy ruling and the two known gotchas.

**One model loaded at a time.** Serve → evaluate → tear down each candidate sequentially; the
16 GB M1 cannot hold two of these at once. The Gemma-4-E4B QAT baseline is the *recorded*
reference (item-17/23 ledgers) — it is **not** re-served alongside a candidate.

## Rulings that apply to BOTH Qwen3.5 candidates (item-24 plan-review, 2026-06-28)

- **Engine:** `mlx-lm 0.31.3` (frozen). 24.2a confirmed mlx-lm's `qwen3_5` module loads these
  multimodal `Qwen3_5ForConditionalGeneration` checkpoints **text-only** (its `sanitize()` drops
  `vision_tower`/`model.visual` weights and builds the `TextModel` from `text_config`).
  **No `mlx_vlm` fallback is needed** — the survey's loader wrinkle is resolved in mlx-lm's favor.
- **Proxy:** non-Gemma ⇒ repair proxy runs in **PASSTHROUGH** — in path for tracing, repair OFF.
  Set `MLX_PROXY_REPAIR=0` (Gemma's `<|tool_call>` parser does not apply to Qwen). Tool-call
  validity is read off Qwen's **native mlx-lm parser**. If the 24.2c smoke shows a *systematic,
  mechanically-fixable* tool-call defect, write a per-model repair shim before the scored run.
- **Quant:** PTQ 4-bit (group-size 64) vs the **QAT** baseline ⇒ any negative verdict carries the
  **quant-method-confound** flag.
- **Thinking-mode OFF (24.3 user decision, recorded covariate `thinking=off`).** Qwen3.5 ships
  thinking-mode ON by default. Serve with `MLX_CHAT_TEMPLATE_ARGS='{"enable_thinking":false}'`,
  which `scripts/mlx.sh` forwards to `mlx_lm.server --chat-template-args` so the DEFAULT request
  path (opencode sends no `chat_template_kwargs`) is no-think — matching the non-thinking Gemma
  baseline and avoiding the wall-clock/OOM blowup (the 9B's first step was 204 s with thinking on).
  **NOTE — editing `chat_template.jinja` does NOT work:** mlx-lm loads the template from the HF
  tokenizer (`tokenizer.json`), not the loose `.jinja`, so the serve-time `--chat-template-args`
  flag is the only reliable lever. Verified: a plain request returns 6 tokens (`finish: stop`)
  instead of 64+ thinking tokens, and tool-calls still emit cleanly.

## Gotcha 1 — the `python3` pyenv wedge (proxy won't start otherwise)

`scripts/mlx.sh up` starts the repair proxy with bare `python3`, which resolves to a broken
pyenv 3.11.6 (`dyld: libintl.8.dylib not loaded`). Front the PATH with a working python3 so the
proxy launches (the mlx-lm **server** runs via `uvx` and is unaffected):

```bash
mkdir -p /tmp/py-shim && ln -sf "$(command -v python3.12)" /tmp/py-shim/python3
export PATH="/tmp/py-shim:$PATH"
```

## Gotcha 2 — pass `MLX_MODEL`/`MLX_REVISION` explicitly (zsh, no word-split)

The Bash tool is zsh; export the serve vars in the same shell as the `make` call.

---

## Candidate 1 — Qwen3.5-4B (A/B FIRST: cleanest same-size swap, comfortable fit)

```bash
export PATH="/tmp/py-shim:$PATH"
export MLX_MODEL="mlx-community/Qwen3.5-4B-MLX-4bit"
export MLX_REVISION="32f3e8ecf65426fc3306969496342d504bfa13f3"   # main @ 2026-03-02, ~3.06 GB
export MLX_PROXY_REPAIR=0       # passthrough — non-Gemma
export MLX_SMALL=0              # don't co-serve the Gemma title model (frees RAM + avoids confound)
export MLX_CHAT_TEMPLATE_ARGS='{"enable_thinking":false}'   # thinking OFF by default (24.3)

make mlx-pull                  # one-time online weight download into mlx-models/Qwen3.5-4B-MLX-4bit
make mlx-up                    # serve on :8080 (proxy passthrough) ; make mlx-status to verify
# ... run the 24.2c smoke / 24.3 A/B against http://127.0.0.1:8080/v1 ...
make mlx-down                  # tear down before the next candidate
```

- Config: `scripts/harness_configs/model-qwen3.5-4b.json`
- Disk: `model.safetensors` 3.034 GB (single shard) + tokenizer ~0.027 GB.

## Candidate 2 — Qwen3.5-9B (A/B SECOND: bigger current model; validate the tight fit first)

```bash
export PATH="/tmp/py-shim:$PATH"
export MLX_MODEL="mlx-community/Qwen3.5-9B-MLX-4bit"
export MLX_REVISION="938d8919941c6e7efd3c7150eff7fe9d12afa631"   # main, ~5.98 GB (2 shards)
export MLX_PROXY_REPAIR=0
export MLX_SMALL=0
export MLX_CHAT_TEMPLATE_ARGS='{"enable_thinking":false}'   # thinking OFF (near-required for 9B wall-clock)

make mlx-pull
make mlx-up
make mlx-down
```

- Config: `scripts/harness_configs/model-qwen3.5-9b.json`
- Disk: 5.350 + 0.600 GB shards.
- **OOM RISK:** ~5.98 GB weights + KV at the 40–50K-token ceiling is tight on 16 GB. If it OOMs
  at serve or mid-rollout, record a **feasibility null** — do not widen the budget.

## Not funded — Phi-4-Mini

No MLX build confirmed in the 24.1 survey (GGUF only); no agent/tool-calling data to rank it.
Recorded maybe/null, not a funded arm. Only revisit if an mlx-lm-loadable build materialises.
