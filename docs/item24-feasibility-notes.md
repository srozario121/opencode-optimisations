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

## 24.3 — first scored run OOM'd: the **4B**, on KV-cache growth (NOT the 9B)

The first 24.3 attempt (`harness-eval/runs/qwen35-4b-K3-r1/`, K=3, thinking OFF) **crashed the
session** ~21:27 on 2026-06-28. Forensics (`mlx-server.log`):
- The candidate **live at the crash was Qwen3.5-4B** (passthrough proxy, `repair=OFF`) — **not**
  the OOM-flagged 9B (the 9B only ran an earlier smoke at 20:17 and was idle).
- The MLX **prompt cache climbed unbounded**: `10 sequences` growing **3.95 → 4.11 → 4.20 → 4.39
  → 4.60 GB** over the final two minutes, then the log **stops abruptly mid prompt-processing**
  (`332/332`) — a Metal OOM kill, no graceful shutdown. Weights (~2.9 GB) + 4.6 GB cache +
  opencode/node + OS crossed the ~16 GB ceiling. Got through ~9 of 11 instances first.

**Correction to the item-24 risk model:** the OOM did **not** come from model size (the small 4B
hit it) nor from K-repeat parallelism (repeats and instances both run *sequentially*, and the
harness already restarts the server on `reason="oom"`). It came from **mlx-lm's per-conversation
prompt/KV cache accumulating across long multi-turn rollouts**. The binding constraint is
**cache bytes × prompt-concurrency**, not parameter count.

**Serialized relaunch (fix shipped):** `mlx.sh` `_start_server` now forwards a new
`MLX_SERVER_EXTRA_ARGS` env (mirrors the `MLX_CHAT_TEMPLATE_ARGS` pattern; survives OOM restarts
because `restart_server()` re-runs `mlx.sh up`). Relaunch driver:
`scratchpad/run_24_3_4b_serialized.sh` exports
`--prompt-concurrency 1 --prompt-cache-bytes 3221225472 (3 GiB) --prompt-cache-size 2` +
thinking OFF, then runs `run --config model-qwen3.5-4b --repeats 3 --timeout 600`. 3 GiB cap +
2.9 GB weights ≈ 6 GB resident, ~10 GB headroom. Per the OOM-null discipline: if an instance
*still* OOMs under the cap, that is a recorded null, not a reason to widen the budget.

**OOM fix VALIDATED + 24.3 4B arm complete (2026-06-29, label `qwen35-4b-K3-serialized`).**
The same config (hash `d4d0d00c543c`) that crashed the whole session ran the full **K=3 × 11 =
33 instance-runs to completion with ZERO OOM** (per-instance reasons: 1 PASS / 29 timeout / 3
tests-failed; `dropped`/`degen` = 0). The 3 GiB cache cap held — no `[recover]` bounce was ever
needed. (Three real bugs were fixed along the way to a clean launch: bare `mlx.sh up` served the
default *Gemma* not Qwen → added serve-layer env + a `/v1/models` guard that aborts on the wrong
id; the proxy hit the pyenv python-wedge → `/tmp/py-shim` python3.12 shim, see
[[mlx-proxy-python-wedge]].)

**Result: pass mean 0.3/11 (spread 0–1 over [1,0,0]).** The lone pass was `sympy` (episode
489.5 s, F2P 1/1 — a real fix, under the 600 s cap). By the harness's own rule a delta must clear
the spread, so **0.3/11 does NOT clear [0–1] → not distinguishable from the Gemma 0/8 baseline**
on pass-rate. The quant-confound flag (PTQ-4bit vs QAT) still applies to the negative.

**But the failure *signature* is materially different from Gemma — and that's the real finding.**
Qwen3.5-4B is **timeout-bound, not no-edit/dropped-bound**: 29/33 runs timed out, yet `dropped`=0,
`made_edit`=0.30 (0.18–0.45), `degen`=0, ~10–11 steps/episode. It **engages and edits** (unlike
the Gemma baseline whose dominant mode is no-tool-stop / `dropped`≈0.38) — it just can't *finish*
within 600 s even with thinking OFF. This mirrors the 24.2 "9B brutally slow" finding: the
Qwen3.5 family is **wall-clock-bound on this 16 GB M1**, not engagement-bound. The obvious next
lever (longer timeout) is OFF the frozen protocol and expensive — record as a covariate, don't
silently widen.

**24.3 9B arm DONE (2026-06-29, label `qwen35-9b-K3-serialized`, driver
`scratchpad/run_24_3_9b_serialized.sh`, cache cap tightened to 2 GiB for the ~6 GB weights).**
**Result pass mean 0.0/11 (spread 0–0 over [0,0,0]).** Failure mode is **100% timeout** — all
**33/33 runs hit the 600 s cap** (episode wall min/median/max = 600.0/600.3/600.9 s), `made_edit`
collapses to **0.03** (vs the 4B's 0.30): the 9B is so slow on this M1 it rarely finishes even a
single edit before the cap. **Zero OOM** — the 2 GiB cap held and the documented 9B OOM-risk never
materialised, because wall-clock kills it long before the KV cache can grow (the binding constraint
for the 9B was always latency, not memory). thinking-OFF did not rescue it.

## 24.3 CLOSED — verdict (iii): no candidate beats Gemma on this harness

| Arm | pass mean | failure signature | OOM | reads as |
|---|---|---|---|---|
| Qwen3.5-4B | 0.3/11 (0–1) | timeout-bound (29/33), but engages + edits (made_edit 0.30), 1 real fix | none | does not clear spread ⇒ ≈ Gemma 0/8 |
| Qwen3.5-9B | 0.0/11 (0–0) | 100% timeout (33/33), barely edits (made_edit 0.03) | none | worse — pure wall-clock death |
| Gemma-4-E4B QAT (baseline) | 0/8 | no-tool-stop / churn (dropped ≈ 0.38) | — | the recorded reference |

**Neither Qwen3.5 candidate clears its spread → (iii): the 4–9B class is a wall *here*; only
BigPickle-class (item 22) clears it — re-justifies the frozen-Gemma choice.** Two caveats sharpen
the negative: (1) **quant-method confound** (PTQ-4bit candidates vs the QAT baseline) — unresolved;
(2) the candidates' wall is **wall-clock/latency, NOT engagement** — Qwen3.5 *engages and edits*
(unlike Gemma's no-tool-stop) but the M1's decode speed at these context lengths can't finish
within 600 s. This is a **hardware-bound** negative specific to this machine, distinct from Gemma's
capability-bound one; a faster host or a higher timeout (off the frozen protocol) could move it.
The serialized-relaunch infra (`MLX_SERVER_EXTRA_ARGS` cache caps + `/v1/models` model-guard +
py-shim) is the lasting deliverable — it made the OOM-crashing run reproducible to completion.
