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

---

## Item 29 — omlx serving-backend probe (2026-06-29) — CLOSED, verdict (i) ADOPT (omlx + VLM-MTP bs=3)

**Why this lives here.** Item 24 closed the *model-swap* lever (the 4–9B class is a wall on this
16 GB M1, and the Qwen wall is **wall-clock/latency**, not engagement). Item 29 attacks that same
latency wall from the **serving-engine** side, with the model held **byte-identical** (no re-quant,
no model swap — item 24's closure stands): would **omlx** (`jundot/omlx` v0.4.4, an Ollama-style
menu-bar MLX server with continuous batching + tiered hot-RAM/cold-SSD KV cache) decode the *same*
Gemma-4-E4B QAT weights meaningfully faster than tuned `mlx-lm 0.31.3`?

### Stage 1 — cheap fast-exit gate: clean PASS on all four checks

omlx installed via `brew tap jundot/omlx && brew trust jundot/omlx && brew install omlx` (Apache-2.0,
vendored into `/opt/homebrew/Cellar/omlx/0.4.4` with its own venv). Served the existing pinned model
dir directly (`omlx serve --model-dir … --port 8081`; models discovered from subdirs).

| Check | Result |
|---|---|
| (a) byte-identical pinned safetensors load | **PASS** — omlx loaded the exact files in-place (log: `gemma4` detected, `VLMBatchedEngine loaded`, weights 6.97 GB). **No GGUF, no re-quant.** `model-00002` sha256 `5c5715…d58d8c` == the git-LFS manifest oid from `mlx.sh pull`. |
| (b) `/v1/models` guard + OpenAI `/v1/chat/completions` | **PASS** — `/v1/models` lists `gemma-4-E4B-it-qat-4bit`; chat-completions returns valid OpenAI JSON. |
| (c) clean Gemma tool-call round-trip | **PASS** — omlx ships a **native `gemma4` tool-call parser** (log: `VLM tool calling enabled: parser=gemma4`); emitted a clean `get_weather {"city":"Paris"}` (`finish_reason=tool_calls`) and completed the full round-trip (tool result → final answer) **with NO repair proxy**. (Refutes the pre-probe risk flag that the README didn't list Gemma among tool-call families.) |
| (d) serve under blocked egress (structural offline) | **PASS** — relaunched under fully **poisoned egress** (dead `HTTP(S)_PROXY`/`ALL_PROXY` → `127.0.0.1:1`, dead `HF_ENDPOINT`, `HF_HUB_OFFLINE=1`, `--no-hf-cache`); freshly **loaded the model + tool-called**, and `lsof -a -p <pid> -iTCP` showed **exactly one socket — the loopback `127.0.0.1:8081` LISTEN, zero outbound connections.** Vendored brew install + local model = structural offline guarantee. |

### Stage 2 — primary metric (decode tok/s), the sole adopt trigger

Measured with the **slope method** (`scratchpad/decode_bench.py`: `(tok_long−tok_short)/(t_long−t_short)`
at max_tokens 320 vs 64, same prompt — cancels prefill/model-load/TTFT), K=5, both backends measured
**proxy-free and serialized same-machine** (16 GB can't co-host two copies), byte-identical weights.

| Backend | slope tok/s (median) | e2e tok/s | vs mlx-lm | ≥ 20%? |
|---|---|---|---|---|
| `mlx-lm 0.31.3` (tuned item-24 caps, engine direct) | 11.09 | 10.98 | — | — |
| omlx `--memory-guard safe` | **12.98** | 12.62 | **+17.0%** | ✗ |
| omlx `--memory-guard aggressive` | 12.49 | 12.20 | +12.6% | ✗ |

All runs hit the 320-token cap (`finish_reason=length` → full-length decode, no early stop); spreads
tight (omlx 12.5–13.0, mlx-lm 11.05–11.57). The `aggressive` tier ≈ `safe` confirms decode is
**MLX-kernel-bound**, not memory-guard-bound → no tier crosses 20%, ruling out a false-negative.

### Stage-2 initial read — plain backend swap is +17 % (below bar)

omlx is a **genuine, consistent ~13–17 % decode win** on the same weights (NOT the "flat" the
shared-MLX-core prior predicted — omlx 0.4.4 bundles newer MLX kernels / a paged cache), at
**confirmed tool-call parity** and **byte-identical weights** — but the *plain* backend swap falls
**below the ≥ 20 % adopt threshold**. That alone would be verdict (ii). **The follow-up tuning A/Bs
below changed the outcome.**

**Reusable deliverable:** the byte-identical-safetensors **serving-backend A/B pattern** — (1) point
the new engine at the *same* pinned model dir + sha256-verify vs the LFS manifest, (2) `/v1` +
tool-call smoke, (3) **poisoned-egress** offline proof via `lsof -a -p <pid> -iTCP`, (4) slope-method
decode A/B serialized same-machine. Bench harnesses: `scratchpad/decode_bench.py`,
`scratchpad/prefix_cache_bench.py`.

### Tuning A/B-1 — tiered prefix cache: ~5.6× faster warm re-prefill (agentic wall-clock)

`--paged-ssd-cache-dir` + `--hot-cache-max-size 4GB` (cache ON) vs `--no-cache` (OFF), on a repeated
~3 200-token shared prefix (system prompt + history) with varying short suffixes — the agentic
multi-turn pattern. Metric = warm-turn prefill (`max_tokens=1`).

| | warm re-prefill | cached_tokens |
|---|---|---|
| cache OFF (`--no-cache`) | **13.38 s** | 0 (full re-prefill every turn) |
| cache ON (SSD + 4 GB hot) | **2.40 s** | 2048/2428 restored |

**~5.6× warm re-prefill speedup** (omlx logs confirm `Partial cache reconstruction`). This directly
attacks the **item-24 agentic wall-clock** (re-prefilling the big stable prefix every turn). Caveat:
this isolates omlx's *own* cache (ON vs OFF); it is **not** an omlx-vs-mlx-lm comparison (mlx-lm has
its own prompt cache) — that head-to-head is unrun. Prefix cache is **ON by default** in omlx.

### Tuning A/B-2 — speculative decoding (VLM-MTP): +20 % decode at block_size=3

A matched **`mlx-community/gemma-4-E4B-it-qat-assistant-bf16`** drafter (gemma4_assistant MTP head,
4 layers / 17.4 M params, tokenizer == target) loaded via `~/.omlx/model_settings.json`
(`vlm_mtp_enabled` + `vlm_mtp_draft_model`). The 6-bit drafter crashed on a quantized-embedding
reshape; **bf16 works**. Block-size sweep (decode slope tok/s, K=5):

| block_size | decode tok/s | acceptance | tok/round |
|---|---|---|---|
| OFF (plain omlx) | 12.98 | — | — |
| 1 | ~3.08 (degenerate) | — | — |
| 2 | 14.88 | 57.6 % | 1.58 |
| **3 (optimum)** | **15.63** | 43.8 % | 1.88 |
| 4 / default | 12.99 (flat) | 29.4 % | 1.88 |

Unimodal peak at **bs=3 = 15.63 tok/s = +20 % over plain omlx, +37–41 % over mlx-lm** (11.39
same-session / 11.09 first-session). bs=1 collapses (per-round overhead, no multi-token payoff); bs≥4
wastes draft compute. **Lossless** (target verifies at temp 0 — primes/essay outputs correct) and
**tool-call parity holds with MTP on** (`get_weather` round-trip clean). The default block_size gives
nothing — **bs=3 is load-bearing**; this is why the plain read missed it.

### Verdict (i) ADOPT — omlx + VLM-MTP bs=3 (with a fairness caveat)

omlx + MTP bs=3 clears the ≥ 20 % bar (**+37 % vs mlx-lm**) at **tool-call parity + lossless output +
offline-capable** (drafter 64–200 MB, locally cached). **Fairness check (the crux):** mlx-lm 0.31.3
*has* `--draft-model`, but **cannot load any available gemma-4 draft** — the only standalone small
gemma-4 (`gemma-4-e2b-it-4bit`, tokenizer-matched) is multimodal and trips mlx-lm's draft loader
(`ValueError: Received 140 parameters not in model` — the **same multimodal-loader wall item 24
documented**; reproduced across `--num-draft-tokens` 2/3/4), and the MTP assistant heads only run
through mlx-vlm (omlx's path), not mlx-lm's generic spec-decode. A hand-built text-only draft was
judged out of scope (2 649-tensor strip + config surgery → a non-off-the-shelf artifact; and a 2 B
draft for a 4 B target rarely beats a purpose-built 17 M MTP head anyway). **So omlx's speculative
advantage rests on its integrated, working MTP path that mlx-lm lacks on this stack — not a proven
raw-kernel gap.** The **plain** backend swap remains +17 % (below bar); the adopt is specifically for
**omlx + MTP bs=3**.

**Adopt config (reproduce):** `omlx serve --model-dir <pinned> --memory-guard safe` + drafter
`mlx-community/gemma-4-E4B-it-qat-assistant-bf16` via `~/.omlx/model_settings.json`
(`vlm_mtp_enabled=true`, `vlm_mtp_draft_model=<path>`, `vlm_mtp_draft_block_size=3`); prefix cache on
by default. **Integration deferred:** `make omlx-up` + the `restart_server` generalization +
`make check` are a follow-up (not built here per the document-now decision). omlx left installed;
drafters cached under `scratchpad/omlx-drafters/`.

### Other machine-tuning levers (source-grounded, secondary)

- **TurboQuant KV cache** (`turboquant_kv_enabled`, bits 2–8): cuts attention KV memory-bandwidth →
  faster **long-context** decode + lower RAM; lossy below 8-bit → needs revalidation; ~flat at the
  bench's short context. Mutually exclusive with MTP.
- **`--memory-guard aggressive`** (tested: no decode change), **`chunked_prefill: true`** — 16 GB-fit,
  marginal speed. **Burst decode** (`burst_decode_mode`) — streaming coalescing only, marginal.
- **Machine hygiene** (free): the A/B ran with Ollama/Spotify/Notion/Chrome resident — quitting them
  reduces memory pressure + variance for both backends.

## Item 30 — omlx gemma4 parity fix (implemented 2026-06-30)

Productionised item 29's drop-in. **Result: omlx+fix matches AND exceeds mlx-lm on real agentic T2.**
K=5 T2 check-frac: **omlx+fix 1.000 (spread 0.000)** vs **mlx-lm 0.882** (0.118) vs old temp-1.0 omlx
**0.776** (0.588). Model byte-identical (item-24 closure stands).

**Root-cause correction — the deficit was a serving-temperature confound, not a tool-call bug.**
The item-30 brief assumed omlx narrates (`[uses grep ...]` instead of a call) because of bad gemma4
*input formatting*. Capturing + diffing the rendered prompt disproved it: **omlx renders via the
checkpoint's own `chat_template.jinja` correctly**; the model emits clean native `<|tool_call>` even
on folded multi-turn requests. The real cause: `mlx_lm.server` defaults a request with no
temperature to **0.0 (greedy)**; omlx defaults to its `settings.json` **1.0**. The harness sends no
temperature, so the item-29/30 omlx-vs-mlx-lm comparison was unfair — omlx ran stochastic → narration
+ natural-language grep patterns + huge variance. **Replaying the captured request at temp 0 makes
narration vanish** (`content: ''`). This refines item 29: omlx's apparent agentic/E2E deficit was the
temp confound, not a tool-call defect.

**Fix (programmatic > prompt, fully):**
- **Cat-2 (narration + variance):** `OMLX_PROXY_DEFAULT_TEMP` — the proxy stamps a temperature (default
  `0`, greedy) on any chat request that omits one, mirroring mlx-lm's server default. Input-side, not a
  prompt lever. Drives narration (mode D) → 0 and T2 → 1.0. The `omlx-toolsteer.json` prompt steer is
  therefore **unnecessary** (kept available, not default).
- **Cat-1 (real omlx output bugs):** `scripts/omlx_repair_proxy.py` strips `<eos>`/`<end_of_turn>`
  leakage and parses native/code/keyval text-tool-calls into structured `tool_calls`; BrokenPipe-safe;
  capture + no-think levers; 17 unit tests. Failure-mode classifier on the K=5 arm: **Cat-1 (A/B/C) = 0,
  Cat-2 (D) = 0**; H/I (over-verbose grep, redundant reads) persist but are shared weak-model quality
  (present on mlx-lm too) and benign — all runs still 17/17.

**Prefix cache re-enabled + parameter sweep (cache-vs-stability resolved).** Greedy temp-parity removed
the OOM-driving narration, so the prefix cache is **ON by default and stable** (0 prefill rejections,
0 evictions, accuracy preserved). 8-arm sweep (cold-vs-warm prefill, max_tokens=1):

| arm | hot-cache | guard | init-blocks | MTP | warm speedup | accuracy |
|---|---|---|---|---|---|---|
| nocache (ref) | — | safe | — | on | 1.21× | 3/3 |
| hot0 (SSD-only) | 0 | safe | 256 | on | 27.1× | 3/3 |
| hot4 | 4GB | safe | 256 | on | 27.4× | 3/3 |
| hot4-balanced | 4GB | balanced | 256 | on | 25.3× | 3/3 |
| **hot4-ib512** | 4GB | safe | **512** | on | **33.2×** | 3/3 |
| hot4-nomtp | 4GB | safe | 256 | off | 24.8× | 3/3 |

Findings: prefix cache gives **~27× warm re-prefill** (cold ~56s → warm ~2s); **hot-cache SIZE is
non-critical** (SSD-only ≈ 4GB) → keep it small (2GB) for 16GB-fit; **`initial-cache-blocks=512` is
the best single lever (+22% → ~33×)**; `memory-guard balanced` gives no gain (keep `safe`); MTP and
the cache coexist. Tuned `omlx.sh` defaults (cache on, hot=2GB, ib=512, guard=safe, MTP) measured
**~35× warm**. Caveat: the cache key is model+path-scoped — a stale dir from a *different* serve path
can stall the first turn (the source of an earlier false "cache-ON OOM"); consistent controller use
avoids it.

**SWE/T3 — no-regression DEMONSTRATED (not the bar; T2 is).** First pass: omlx+fix hung at **step=0**
(0/2, timeout). Root cause was NOT the model — the proxy **forced non-streaming** (to repair the full
turn), so opencode received nothing until a long T3 generation completed → 600s timeout. Built a
**streaming-repair proxy** (`OMLX_PROXY_STREAM_REPAIR`, default on): forward omlx's SSE incrementally
(mirror mlx-lm's stream-through `_passthrough` — upstream status+headers verbatim, raw lines via
`readline`), stripping `<eos>` per content delta; text-tool-call repair is skipped on the stream path
because temp-parity makes omlx emit native structured `tool_calls`. **Re-test:** T2 stays **3/3 (1.0)**
via streaming (tool calls intact — the key risk, cleared); T3 now **engages step 0→10, terminates
naturally at 509s, FAIL (no-edit)** — i.e. it explores via grep/read but can't produce a fix, the
shared capability wall (mlx-lm is 0/8 on T3 too; a serving fix cannot move model capability). So
omlx+fix engages the hard tier identically to mlx-lm. The prefix cache also confirmed working on the
T3 ~8k context (43s→1.4s warm). Caveat: the streaming path can't repair a tool-call emitted as TEXT
mid-stream, but at temp 0 that mode is 0 (omlx structures tool calls natively).

**T3 trajectory deep-dive + grep-tool fix (2026-06-30).** Read the sympy-21627 trajectory step-by-step.
The model engaged the whole episode but **never read or edited a file** — and the cause was a TOOL
defect, not (initially) the capability wall: it called `grep {include:"sympy/", pattern:"is_zero"}`
meaning "search the sympy/ directory", but the custom grep tool (`admin/.opencode/tools/grep.ts`) feeds
`include` straight to `rg --glob`, and `--glob sympy/` matches **zero files** → every grep returned a
false **"No matches"** (`is_zero` actually has 954 matches). The model concluded the symbol didn't exist
and burned the budget wandering. **Fix (programmatic, kept):** wildcard-free `include` now pushes BOTH
`--glob base` and `--glob base/**`, so a directory value resolves to its subtree (`sympy/`→954,
`sympy/core`→215) while real globs (`*.py`) and specific filenames (`setup.py`) are unaffected. **Effect:**
the re-test advanced from *no-edit/wandering* → **located `is_zero` + read `expr.py:618`** (first time it
reached the read stage), then hit the 600s budget mid-investigation (FAIL timeout, no edit). Honest
caveat: that particular re-run happened to use file-scoped includes (work on both tool versions), so a
single run doesn't cleanly isolate the fix's causal effect — but the fix is an independently-validated
strict robustness win that helps every grep user incl. T2. **Verdict on "can prompt/tool changes pass
T3": NO.** The fix makes the test *fair* (removes a spurious early blocker) but the residual wall is the
4B's latency+capability ceiling — out of budget mid-read at 600s, and per items 16/19/20/23/24 a 4B does
not author the correct fix regardless (only BigPickle clears real T3). A clean @1800s isolation was
attempted twice and **failed at the infra level** (run 1: prefix-cache corruption — a 4-layer MTP block
polluting the 24-layer main cache → invalidate/abort thrash; run 2 after cache-clear+restart: opencode
never issued a request, racing the harness's server-reload), itself consistent with the item-24
serving-fragility-on-16GB theme. Net: tool fix kept; T3 stays a genuine-wall FAIL, which is the cleaner
no-regression story (fair test, capability/latency wall holds).

**Deliverables:** `scripts/omlx.sh` controller + `make omlx-{pull,up,down,status,serve}`;
`restart_server` generalized via `HARNESS_SERVE_BACKEND={mlx,omlx}`; hardened proxy + 17 tests;
sweep/measurement tooling under `scratchpad/` (`cache_bench.py`, `cache_stats.py`, `t2_stats.py`,
`failure_modes.py`, `run_30_*`). `make check` + harness selftest green.

**Reproduce:** `make omlx-up` (cache on, MTP, temp-parity all default) → point opencode/harness at
`http://127.0.0.1:8080/v1`. Anchors: `OMLX_CACHE=0` (no-cache), `OMLX_MTP=0` (no drafter),
`OMLX_PROXY_DEFAULT_TEMP=""` (disable temp stamp).

## Item 31 — clean-serving T3 isolation (infra built 2026-06-30; measurement pending)

Item 30's two contaminated @1800s T3 runs left the latency-vs-capability attribution open. Item 31
makes every T3 measurement begin from a confirmed-healthy, freshly-loaded server with a cleared prefix
cache, so a result can no longer be poisoned by dirty serving state. The infra is now in place; the
clean re-measure (31.2) and any lever probe (31.3) are the remaining live-run work.

**Infra deliverables (this session):**
- **`scripts/run_t3_clean.sh`** (31.0) — the clean-cycle runner: for each repeat it does
  `omlx down` → clear **all** prefix-cache namespaces → `omlx up` → **health-gate** → run the instance,
  recording the server PID + model-load (process-start) timestamp for after-the-fact contamination
  audits. Sets `HARNESS_SERVE_BACKEND=omlx` + `HARNESS_NO_MIDRUN_RESTART=1`. Usage:
  `scripts/run_t3_clean.sh sympy__sympy-21627 600` and `… 1800` (the 31.2 latency-vs-capability pair).
- **Health-gate** (`scripts/omlx.sh health` / `make omlx-health`) — polls `/v1/models` until a model is
  served **and** runs a tiny warmup completion through the proxy. The warmup forces the model's first
  decode and exercises the whole proxy→omlx path, so opencode's first real call can't race a cold
  upstream (the **server-reload race**, item-30 run 2 — "0 requests for the whole episode").
- **Proxy upstream-readiness retry** (`omlx_repair_proxy.py`, `OMLX_PROXY_UPSTREAM_READY_S`, default
  90s) — a refused/unavailable upstream during a (re)start is retried (connect only, body re-sendable)
  rather than 502-ing the first call. Belt-and-suspenders with the health-gate. +3 unit tests.
- **Prefix-cache namespacing by MTP config** (`omlx.sh`) — the SSD cache dir is now keyed by the layer
  config (`omlx-cache-mtp3` / `omlx-cache-nomtp`), so a cache written by a 4-layer-MTP serve can never be
  re-read by a 24-layer serve → eliminates the **layer-count collision** ("block has 4 layers, expected
  24", item-30 run 1) across serve paths. The wrapper also clears every namespace per run.
- **`HARNESS_NO_MIDRUN_RESTART=1`** (`harness_eval.py`) — disables the OOM self-heal `restart_server`
  inside `_score_subset` so the external wrapper owns the full server lifecycle and nothing bounces the
  server mid-pass.

`make check` + harness selftest green.

### First clean run — 600s (2026-06-30): infra confirmed, verdict still pending the 1800s

`scripts/run_t3_clean.sh sympy__sympy-21627 600` (label `item31-clean-t3-600`), full clean cycle:

- **Infra works.** Health-gate passed (warmup completion **10.5s**, model loaded), audit captured
  (`server-pid=29371 loaded-at=18:20:05`), episode ran the full 600s and exited 0. **Server-reload race:
  gone** (no step-0 hang — first tool at 35.8s/step 1, proxy saw traffic throughout). **No OOM, no fatal,
  no abort** in the server log.
- **Layer-count collision REFRAMED — it is within-serve and benign, not the item-30 thrash.** The
  "Cache layer count mismatch: block has 4 layers, expected 24. Invalidating cache hit." WARNING still
  fires (3×) on a *single* MTP-enabled serve — so the MTP drafter's 4-layer KV blocks DO share the
  prefix-cache keyspace with the 24-layer main model (the namespacing fix only covers the *cross-serve*
  stale-dir case). **But on a clean, freshly-cleared, namespaced cache it is 3 benign WARNINGs alongside
  13 successful cache hits — no abort, no thrash.** Item-30 run-1's "invalidate/abort thrash" was the
  *severe* form, driven by a stale/contaminated dir; the clean-start + namespacing removed it. Residual
  = occasional lost cache hit (minor perf), not a correctness/crash bug. (Open lever: MTP-vs-cache
  mutual-exclusion or an upstream drafter-block namespace would eliminate even the benign invalidations.)
- **600s result = FAIL (timeout), made_edit=False, steps_to_first_edit=None**, 6 steps / **5 tool-call
  rounds** in 600s (~120s/round — latency-dominated; output_tokens=473), `degenerate_loop=False`, one
  `glob` tool error. It engaged and explored cleanly but **ran out of budget mid-exploration before
  reaching the edit stage** — so 600s is *latency-starved* and cannot yet separate verdict (ii) from
  (iii): no edit reached means neither "correct edit" (ii) nor "wrong edit" (iii). **The decisive test is
  the 1800s extended-budget run** (`scripts/run_t3_clean.sh sympy__sympy-21627 1800`): reaches a *correct*
  edit ⇒ latency-bound (ii); reaches the edit stage with a wrong/no edit ⇒ capability wall (iii).

### Decisive 1800s run (2026-06-30) — verdict (iii) CAPABILITY WALL

`scripts/run_t3_clean.sh sympy__sympy-21627 1800` (label `item31-clean-t3-1800`), clean cycle (health-gate
warmup 10.1s, `server-pid=33454`):

- **Terminated NATURALLY at 874.4s** with reason **`no-edit`** — `timed_out=False`, ~925s of the 1800s
  budget left **unspent**, `saw_exit_loop=True` (the model hit its own stop condition). 14 steps / **12
  tool-call rounds** (vs 5 at 600s), `output_tokens=1185`, `degenerate_loop=False`, one `glob` error.
- **Still made NO edit** (`made_edit=False`, `model_patch_bytes=0`, `steps_to_first_edit=None`).
- Server healthy throughout: 9 benign layer-mismatch WARNINGs, **0 fatal/OOM/abort**, 35 cache hits.

**This is verdict (iii) CAPABILITY WALL, decisively.** With 3× the budget on a clean, healthy server the
model was **not** latency-bound — it had ~925s to spare and *chose to stop* without attempting a fix. The
600s "latency-starved" appearance was a small-budget artifact; the true shape is **explore-then-give-up**
(12 rounds, no edit, natural termination) — the same regime as mlx-lm's 0/8 T3 and item-30's omlx T3
(engage → terminate → FAIL no-edit). The grep-fix (item 30) made the test *fair* (it reaches the
exploration/read stage); the residual wall is the 4B's inability to author the fix. Re-confirms items
16/19/20/23/24: **only BigPickle clears real T3.**

**31.3 (lever probe) is moot and skipped:** its gate was "only if 31.2 shows it's reachable." 31.2 shows
the threshold is NOT reachable on this 4B (capability, not latency, and not budget) — no cheap T2-safe
lever closes a capability wall, so there is nothing to probe. **Item 31 CLOSED — verdict (iii).**

### Addendum (2026-06-30) — codemode + reproduce-first prompt iteration: decomposing the wall

Follow-up question: *can codemode (item 21) or an iteratively-tuned prompt climb the sympy-21627 wall?*
Read the baseline 1800s trace first: the 4B correctly identifies the bug (is_zero recursion on
`cosh(acos(...))`) and even names the repro expression, but **never runs it** — it guess-localizes by
reading, anchors on the WRONG file (`expr.py`), and stops. The gap is localization+authoring, not budget.

- **Codemode (sandbox) = wrong vehicle.** The item-21 exec sandbox is `no import/open/eval`
  (`codemode_exec.py:24`) — a usability sandbox for parse/transform tasks. It **cannot `import sympy`**,
  so it cannot reproduce a bug in the repo under test. The *generalizable* idea behind it (execute code →
  read the traceback to localize) is right, but the vehicle is the already-present `bash`+`python`,
  triggered by a prompt, not the Monty sandbox. (No run needed — structural.)
- **New gated harness hooks (off by default; baseline byte-unchanged):** `HARNESS_AGENT_VENV_ON_PATH=1`
  prepends the instance's prepared venv to the agent's PATH so `python repro.py` actually runs (the host
  `python3` is the broken-pyenv libintl shim — without this the agent's repro dies on a dyld error);
  `HARNESS_PROMPT_APPEND_FILE` appends guidance to the TASK prompt; `harness_configs/repro-first.json`
  carries the additive `rules_append` variant.
- **Localization wall: CLIMBED (robust).** A reproduce-first directive **placed in the task prompt** (not
  AGENTS.md — the 4B ignores the AGENTS.md append, iter 1) + a working `python` makes the model reproduce
  (`python -c …is_zero`), read the traceback, and open the RIGHT file
  (`functions/elementary/complexes.py`, the `signsimp(arg.conjugate())` recursion driver) in ~every run.
  This is a genuine novel positive for this repo: a prompt lever that moves the T3 trajectory by changing
  TOOL BEHAVIOUR, not by hoping the model reasons better.
- **Authoring wall: HELD (high-variance ceiling).** 7 clean runs on sympy-21627:
  - iter2 (repro directive): right file, but a *destructive* edit (deletes `sqrt(...)`) → F2P 0/1.
  - iter3 (+understand+verify-retry, 5-step): **F2P 1/1** (bug fixed!) but **P2P 25/26** — removed
    `signsimp(arg.conjugate())`, which re-broke `test_issue_14238`. Closest miss ever on a real T3.
  - iter4 (+run-existing-tests + "targeted guard, don't delete", 6-step): **no-edit** — the heavier
    prompt froze the 4B at authoring (item-19 *verbose-hurts*, single draw).
  - iter5 (5-step + ONE light regression line): created `repro.py` only, **no source edit** → P2P 26/26,
    F2P 0/1 (non-regressing because it changed nothing).
  - **v3 K=3 confirmation:** iter3's F2P 1/1 did **NOT replicate** — the two re-runs of the identical
    prompt were both **no-edit**. So {F2P1/1+1regress, no-edit, no-edit} ⇒ the bug-fix was a **lucky
    high-variance draw**, caught by K=3 (the repo's discipline working as designed). **Zero clean PASSES
    in 7 runs.**
- **Verdict:** codemode cannot climb it; the reproduce-first prompt lever **robustly climbs localization**
  but **authoring a correct non-regressing fix is the residual 4B capability ceiling** — modally no-edit,
  best draw fixes-but-regresses, heavier prompts regress further. Re-confirms items 16/19/20/23/24/31
  (only BigPickle clears real T3), now with a sharper localization-vs-authoring decomposition. Lasting
  reusable deliverables: the localization lever (`repro-first` strategy) + the two gated harness hooks.
  *(Caveat: a repro.py the agent leaves in the checkout counts as the model patch → `made_edit=True` can
  be a false positive when no source was edited; F2P/P2P scoring is unaffected since repro.py runs no
  tests. Future repro-first runs should exclude `repro.py` from the patch, like AGENTS.md/opencode.json.)*

### Addendum 2 (2026-06-30) — tailored tool / tailored codemode for tool-calling + context-discovery efficiency

Follow-up: *can a tailored tool / tailored codemode make tool-calling and relevant-context discovery more
efficient?* Built a **`localize` tool** — `scripts/localize_repro.py` (helper, 4 unit tests) +
`.opencode/tools/localize.ts` — a "tailored codemode" that, unlike the import-LESS item-21 Monty sandbox,
runs Python IN THE INSTANCE VENV: one call runs the failing snippet, parses the (~1000-frame) traceback,
surfaces the recursion **cycle** by frame-frequency (consecutive-collapse misses a multi-frame cycle),
and returns source windows + a domain-frame "candidate fix site". Gated: installed into the global
opencode tools dir only for the flagged run (removed on exit); driven by `HARNESS_AGENT_VENV_ON_PATH`
(sets `HARNESS_VENV_PY`) + `LOCALIZE_HELPER`.

**Context discovery / tool-calling efficiency — LARGE, CLEAN WIN (answer: yes).**

| Arm | tool rounds | wall | reach-edit | localization |
|-----|------------:|-----:|-----------:|--------------|
| baseline (grep/read) | 12 | 874s | never | **wrong file** (`expr.py`) |
| reproduce-first PROMPT (iter3) | 11 | 1577s | step 6 | right file (slow, high-variance) |
| `localize` tool + terse nudge | **3** | **68–97s** | never (narrate-stop) | **right cycle+fix site, 1 call** |
| `localize` tool + authoring scaffold | 16–17 | 1800s (timeout) | **step 3** | right + targeted guard attempt |

- The tool gets adopted (the 4B calls it first). One deterministic call replaces ~8 grep/read rounds and
  lands the CORRECT location (`complexes.py` `Abs.eval`) where baseline grep-and-guess hit the wrong file.
  Discovery **12 rounds/874s → 3 rounds/~80s**; reach-to-edit (with scaffold) at **step 3** vs step 6
  (verbose prompt) vs never (baseline). Two weak-model usability gaps found+fixed: (i) the 4B writes
  import-less snippets → helper now **auto-imports the repo's top-level package**; (ii) an over-confident
  "fix site" anchored it on the `__abs__` operator → driver now skips operator/dispatch/cache dunders and
  points at `complexes.py:621 eval`.
- **But efficiency does NOT move the pass rate — authoring is still the ceiling.** Given efficient correct
  localization the model either narrates the fix and stops (terse nudge: `localize>read>read`, no edit),
  or (with the scaffold) reaches a *targeted guard attempt* at step 3, the guard fails verification, it
  reverts, then **degenerates into re-issuing a stale edit 11× (oldString no longer matches) until
  timeout** — F2P 0/1 both K=2. A distinct weak-model **edit-retry tool-churn loop** (cf. item 16)
  surfaced here, separate from the authoring failure.
- **Verdict:** tailored tools/codemode make tool-calling + context discovery **dramatically more efficient
  and more reliable** (a reusable win for any reproducible-bug T3 — the model reaches the right edit site
  in ~80s/1 call), but they **do not climb the wall**: the residual ceiling is authoring a correct fix,
  which efficient discovery cannot supply. The tool makes the 4B fail *faster and at the right file*, not
  pass. Reusable deliverables: the `localize` tool + helper (gated, off by default; `make check` green,
  26 tests).

**Localize tool × BigPickle (strong online model) on T3 — the compounding test (2026-07-01).** The tool is
model-agnostic (executes locally; only the LLM is remote), so it drops onto the item-22 online arm
(`online-bigpickle`, `external_provider`, free opencode-zen gateway, $0). A/B (tool vs no-tool, working
`python` both) on two crash-shaped T3 instances:

| arm | reason | F2P | P2P | rounds | wall |
|-----|--------|-----|-----|-------:|-----:|
| 22714 BigPickle + localize | **ok** | **1/1** | 11/11 | 30 | 200s |
| 22714 BigPickle base | ok | 1/1 | 11/11 | 37 | 200s |
| 21627 BigPickle + localize | tests-failed | 0/1 | 26/26 | 40 | 224s |
| 21627 BigPickle base | **timeout** | 0/1 | 0/26 | 107 | 600s |

- **The tool compounds with a capable model → a real PASS**: BigPickle + localize cleanly solves T3
  **22714 (F2P 1/1)** — the capstone the 4B could never reach.
- **Its efficiency value scales with LOCALIZATION difficulty.** On 22714 (easy to localize — a plain
  `Point2D` crash) BigPickle finds it alone, so the tool is a *marginal* win (37→30 rounds, ~200s either
  way). On 21627 (hard — a 625-frame recursion cycle through generic machinery) the tool is a *large* win:
  baseline **drowned in 107 rounds and TIMED OUT** (P2P 0/26), the tool arm **finished in 40 rounds/224s,
  clean (P2P 26/26)**.
- **The tool changes efficiency, not capability.** 22714 passes with or without it; 21627 fails with or
  without it (its correct fix eludes even BigPickle — the item-27.2b verification/completeness wall: it
  authored a thoughtful *non-regressing* fix `sqrt(...)→sqrt(...,evaluate=False)` that still doesn't pass
  F2P). Cross-model summary: the tailored `localize` tool is a genuine **efficiency/reliability primitive
  for reproducible-error bugs whose value grows with localization difficulty** — it makes the weak 4B fail
  faster at the right file, and makes the capable model reach the fix site faster and avoid
  exploration-timeouts (turning a 107-round timeout into a 40-round clean finish) — but authoring the
  correct fix is orthogonal to it and remains the model-capability ceiling.
