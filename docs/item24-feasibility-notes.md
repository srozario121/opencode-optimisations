# Item 24.2 — feasibility staging notes (small-model survey)

Measured on this machine (16 GB M1, mlx-lm 0.31.3), 2026-06-28. These are the **build-time
feasibility-gate** results that precede the 24.3 scored A/B. Per the Evidence policy, a candidate
that serves + emits valid tool-calls passes the gate (→ eligible for 24.3); one that cannot is a
recorded **feasibility null**. The gate does NOT adopt/reject — only the 24.3 local K≥3 run does.

## Candidates staged

| Candidate | Repo (mlx-community) | Rev (pinned) | Disk | Quant |
|---|---|---|---|---|
| Qwen3.5-4B | `Qwen3.5-4B-MLX-4bit` | `32f3e8ecf65426fc3306969496342d504bfa13f3` | 2.9 GB (1 shard) | 4-bit, group 64 |
| Qwen3.5-9B | `Qwen3.5-9B-MLX-4bit` | `938d8919941c6e7efd3c7150eff7fe9d12afa631` | 5.98 GB (2 shards) | 4-bit, group 64 |

Both repos exist (HF API 200). Serve recipes: `scripts/harness_configs/SERVE-RECIPES-item24.md`.
Configs: `scripts/harness_configs/model-qwen3.5-4b.json`, `…-9b.json`.

## 24.2a — loader feasibility: the survey's `mlx_vlm` risk is RETIRED

The v2 survey flagged that Qwen3.5 small models are natively multimodal and their 4-bit builds
"may require `mlx_vlm`, not `mlx_lm`" — a potential feasibility null against the frozen engine.

**Resolved in mlx-lm's favor.** Both checkpoints are the multimodal
`Qwen3_5ForConditionalGeneration` (config has `vision_config` + `text_config`, image/video
tokens), BUT mlx-lm 0.31.3 ships a `qwen3_5` model module whose `Model.sanitize()`:
- drops every `vision_tower` / `model.visual` weight,
- remaps `model.language_model.*` → `language_model.model.*`,
- builds only the `TextModel` from `text_config`.

So mlx-lm serves these VLM checkpoints **text-only** on the frozen 0.31.3 path. **No `mlx_vlm`
fallback needed** — the engine-scope gated fallback (item-24 design decision) is not triggered.
Confirmed in practice: `make mlx-up` loaded the 4B (~2.9 GB), `/v1/models` healthy in ~10 s.

## 24.2c — Qwen3.5-4B feasibility gate: **PASS**

- **(a) Serves on mlx-lm 0.31.3:** ✅ loads text-only, healthy, ~2.9 GB resident class.
- **(b) Valid tool-calls through the passthrough proxy:** ✅ clean OpenAI-format `tool_calls`
  (`finish_reason: tool_calls`, correct fn name, valid JSON args, id). mlx-lm's **native Qwen
  parser** produces them — **no repair shim needed** (proxy stays `MLX_PROXY_REPAIR=0`).
- **End-to-end harness smoke** (`sympy__sympy-13043`, tier 4): the model **engaged fully** —
  12 steps of valid multi-turn tool calls. It hit a wall-clock cap at **240 s** without landing
  the fix (F2P 0/1), but that cap was an artificially tight smoke `--timeout 240`; the harness
  default is **600 s**. Engagement + valid tool use = feasibility PASS; pass/fail on instances is
  24.3's question.

### ⚠ Finding that becomes a 24.3 design fork — thinking-mode is ON by default
Qwen3.5 runs with **thinking mode ON by default**: a trivial "reply HELLO_QWEN" prompt spent
**155 completion tokens** (reasoning) before answering; with `chat_template_kwargs:
{enable_thinking:false}` it was **6 tokens**. Implications for 24.3:
- **Latency:** ~13–20 s/step in the smoke (12 steps / 240 s) → slow; raises timeout-fail risk.
- **OOM:** extra reasoning tokens push toward the 40–50 K Metal-OOM ceiling — sharper on the 9B.
- **Like-for-like:** the Gemma baseline is not a thinking model, so "defaults on both" is not
  obviously apples-to-apples. **Decision needed before the 24.3 run:** thinking ON (opencode
  default, honest-but-slow) vs OFF (`enable_thinking=false`, faster/OOM-safer, deviates from
  raw defaults). Carry the choice as a recorded covariate either way.

## 24.2c — Qwen3.5-9B feasibility gate: **PASS (but very slow)**

- **(a) Serves on mlx-lm 0.31.3:** ✅ loads text-only (same `qwen3_5` sanitize path), healthy.
  Resident ~4.6 GB at low context (RAM free 79% → 50% after first inference). **No OOM at
  tier-4 context** during the smoke (RAM held ≥44% free); the 40–50 K-ceiling KV stress on
  larger instances is **untested** — keep the OOM-null discipline for 24.3.
- **(b) Valid tool-calls through the passthrough proxy:** ✅ clean OpenAI-format `tool_calls`,
  native Qwen parser, no shim.
- **End-to-end harness smoke** (`sympy__sympy-13043`, tier 4, 300 s cap): engaged but
  **brutally slow** — **step 0 alone took 204 s** (a large thinking-mode reasoning block before
  the first tool call) vs ~80 s for the 4B; only 4 steps before the cap. FAIL (timeout), F2P 0/1.

### Implication: thinking-mode makes the 9B marginal on wall-clock
At ~half the 4B's decode speed, thinking-on means the 9B spends minutes per turn. At the default
600 s cap it would clear only ~8–10 steps — likely too few to land most fixes — and a K≥3 full
battery would be very expensive in wall-clock. This **sharpens the thinking-mode fork**: for the
9B especially, `enable_thinking=false` is close to a feasibility precondition, not just a tuning
knob.

## Summary — 24.2 verdict

| Candidate | Serves (mlx-lm 0.31.3) | Valid tool-calls | Engages | OOM @ tier-4 | Gate |
|---|---|---|---|---|---|
| Qwen3.5-4B | ✅ text-only | ✅ native parser | ✅ 12 steps/240 s | none | **PASS** |
| Qwen3.5-9B | ✅ text-only | ✅ native parser | ✅ 4 steps/300 s (step 0 = 204 s) | none @ tier-4 | **PASS, slow** |

Both eligible for 24.3. `mlx_vlm` fallback NOT needed; repair shim NOT needed (passthrough proxy
suffices). **Open 24.3 design decision: thinking ON vs OFF** (covariate to record either way) —
material for the 4B, near-blocking for the 9B on wall-clock. Smoke timeouts (240 s/300 s) were
deliberately tight; the scored run uses the 600 s default.
