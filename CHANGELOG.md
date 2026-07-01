# Changelog — opencode-optimisations

Completed work, moved out of `TODO.md`. Newest milestones first within each
group. Items keep their original ledger numbers (1–15) for cross-reference with
claude-mem memory and the `docs/` research.

> **History note.** This repo was *extracted* (item 15) from a larger personal
> toolkit (`admin`), where the original 2,475-line TODO.md (items 1–16) lived.
> That ledger did not come across — items 1–16 were reconstructed from claude-mem
> cross-session memory so the numbering stays continuous. Items 1–15, **16, 17, 18,
> 19, 21, and 22** are recorded here; the open work (item 20) remains in `TODO.md`.

## Done (items 1–15)

- **1–7** — Serving stack, model selection, MLX tuning, repair proxy,
  token-reduction `read`/`grep` tools, Jaeger/OTel tracing.
  (See `README.md` + `docs/opencode-local.md`.)
- **8** — Fixed the model (Gemma 4 E4B QAT) and serving engine (mlx-lm via MLX).
- **9** — Exhausted the **serving-engine** lever (MLX tuning). Nothing beat baseline.
- **10** — Compared whole inference engines (`docs/local-inference-engines-research.md`).
  Nothing beat baseline.
- **11** — Harness-engineering lever survey (`docs/harness-engineering-research.md`).
  Ranked the 7 opencode-side lever categories; top-4 single-lever shortlist:
  **L1** minimal toolset → **L2** lower temperature → **L3** terser per-agent prompt →
  **L4** stale-output pruning.
- **13** — Implemented harness levers and **adopted as baseline**: on-demand
  **skills mechanism** (~1.6 KB situational guidance loads via the skill tool, off
  the hot path), **system-prompt diet**, and a **hard read-cap** in
  `.opencode/tools/read.ts` (targets the 40–50K Metal-OOM ceiling). Net cost
  +39 resident tokens (+0.7%) / +0.18s TTFT (+0.6%) — kept for maintainability.
  Generator defaults `MLX_SKILLS=1`, `MLX_READ_CAP=1`.
- **14** — "Signal-producing harness tests": improved the **micro-suite 0.62 → 1.00**.
  ⚠ **Did NOT transfer to the full harness** — see item 16 (in `TODO.md`).
- **15** — Extracted the opencode stack into this standalone published repo. **= this repo.**

## Done (items 16, 17, 18, 19, 20, 21, 22, 23, 24, 29, 30, 31)

- **31** — **Push one T3 item past the threshold (sympy-21627) — closed 2026-06-30, verdict (iii)
  CAPABILITY WALL.** Built clean-per-test serving infra so a T3 result can no longer be poisoned by dirty
  state (item 30 had two contaminated @1800s runs): `scripts/run_t3_clean.sh` (down → clear ALL prefix-cache
  namespaces → up → **health-gate** → run → PID/load-ts audit), `scripts/omlx.sh health` / `make omlx-health`
  (`/v1/models` poll + a warmup completion through the proxy), a proxy **upstream-readiness retry**
  (`OMLX_PROXY_UPSTREAM_READY_S`, +3 tests), prefix-cache-dir **namespacing by MTP config**, and a
  `HARNESS_NO_MIDRUN_RESTART=1` gate in `harness_eval.py`. **Both item-30 infra failures resolved on the
  clean path:** the server-reload race is gone (health-gate warmup ~10s, episode engages at step 1; proxy
  retry available), and the layer-count collision is reframed as *within-serve* (the 4-layer MTP drafter
  shares the prefix-cache keyspace with the 24-layer main model) but **benign on a clean cache** (3 WARNINGs
  / 13 hits @600s, 9 / 35 @1800s, 0 fatal/OOM/abort) — item-30's "invalidate/abort thrash" was the severe
  stale-dir form, removed by clean-start + namespacing. **Measurement:** 600s → FAIL timeout, 5 tool-rounds,
  no edit (latency-starved, inconclusive). **1800s (decisive) → FAIL `no-edit`, terminated NATURALLY at 874s
  with ~925s budget UNSPENT** (`timed_out=False`, `saw_exit_loop=True`), 12 tool-rounds, `made_edit=False`,
  `model_patch_bytes=0`, no degenerate loop. With 3× budget on a healthy server the model was NOT
  latency-bound (time to spare) and attempted no edit ⇒ the 4B capability ceiling (explore-then-give-up),
  the same regime as mlx-lm's 0/8 T3. 31.3 (lever probe) moot/skipped (gate was "only if reachable").
  Re-confirms items 16/19/20/23/24 — only BigPickle clears real T3. Lasting deliverable: the clean-serving
  isolation infra + the two infra fixes. (`docs/item24-feasibility-notes.md` item-31 §.)
  **Addendum (codemode + reproduce-first prompt iteration):** a trace-driven follow-up decomposed the
  wall into *localization* + *authoring*. Codemode (item-21 sandbox) is the wrong vehicle — it is
  import-less (`no import/open/eval`), so it cannot reproduce a bug in the repo under test. But a
  reproduce-first directive placed in the TASK prompt (not AGENTS.md, which the 4B ignores) + a gated
  working-`python` enabler (`HARNESS_AGENT_VENV_ON_PATH`) **robustly climbs localization**: the model
  reproduces (`python -c …`), reads the traceback, and opens the right file (`complexes.py`'s
  `signsimp(arg.conjugate())` recursion driver) where read-based localization always failed. **Authoring
  held as a high-variance ceiling:** 7 runs, best single draw fixed F2P 1/1 but regressed 1 of 26 P2P
  (`test_issue_14238`), heavier prompts regressed to no-edit (verbose-hurts), and a K=3 re-run of the
  best prompt did NOT replicate the F2P win (no-edit ×2) — a lucky draw caught by K=3. Zero clean PASSES.
  New gated, off-by-default harness hooks (`HARNESS_AGENT_VENV_ON_PATH`, `HARNESS_PROMPT_APPEND_FILE`,
  `harness_configs/repro-first.json`) are the reusable deliverable.
  **Addendum 2 (tailored tool / tailored codemode for efficiency):** built a `localize` tool
  (`scripts/localize_repro.py` + `.opencode/tools/localize.ts`) — a tailored codemode that runs in the
  INSTANCE VENV (the import-less Monty sandbox can't), reproduces in one call, and returns the recursion
  cycle + source windows + fix site. **Context-discovery efficiency is a large clean win:** 1 deterministic
  call replaces ~8 grep/read rounds and lands the CORRECT file (`complexes.py`) where baseline grep-guess
  hit the wrong one — discovery 12 rounds/874s → 3 rounds/~80s; reach-to-edit (with an authoring scaffold)
  at step 3 vs step 6 (verbose prompt) vs never (baseline). The 4B adopts the tool reliably (auto-import
  fixes its import-less snippets). **But it does NOT move the pass rate:** given efficient correct
  localization the model narrates-and-stops (terse) or reaches a targeted guard attempt then edit-churns
  to timeout (scaffold) — F2P 0/1, a degenerate edit-retry loop surfaced. Tailored tools make the 4B fail
  faster and at the right file, not pass — authoring stays the ceiling. Deliverables (gated, off by
  default; `make check` green, 26 tests): the `localize` tool + helper.
  **Localize × BigPickle (compounding test, 2026-07-01):** the tool is model-agnostic (runs locally), so
  it drops onto the item-22 online arm (free opencode-zen gateway, $0). BigPickle + localize **PASSES T3
  22714 (F2P 1/1)** — the tool compounds with a capable authoring model to a real green the 4B never
  reached. Its efficiency value scales with localization difficulty: marginal on easy-to-localize 22714
  (37→30 rounds), but on hard-to-localize 21627 the baseline **drowned in 107 rounds and TIMED OUT** while
  the tool arm **finished in 40 rounds/224s clean (P2P 26/26)**. It changes efficiency, not capability:
  22714 passes either way, 21627 fails either way (even BigPickle can't author 21627's fix — item-27.2b
  verification wall). Net: `localize` is an efficiency/reliability primitive for reproducible-error bugs
  whose payoff grows with localization difficulty; authoring correctness is orthogonal and model-bound.

- **30** — **omlx gemma4 parity fix — implemented 2026-06-30, verdict: PARITY+ via a pure
  PROGRAMMATIC fix.** Productionises item 29's drop-in: omlx now MATCHES AND EXCEEDS mlx-lm on real
  agentic T2 success. **Headline K=5 T2: omlx+fix = 1.000 (spread 0.000)** vs mlx-lm 0.882 (spread
  0.118) vs the old temp-1.0 omlx 0.776 (spread 0.588). Model byte-identical (item 24 closure stands).
  - **Cat-2 was a misdiagnosis; the real root cause is a serving-temperature confound.** The TODO
    assumed omlx narrates (`[uses grep to ...]` instead of a call) because its gemma4 *input
    formatting* uses non-standard markers. Reading omlx source + **capturing/diffing the rendered
    prompt** disproved that: omlx renders via the checkpoint's own `chat_template.jinja` correctly and
    the model emits clean native `<|tool_call>` even on folded multi-turn requests. The real cause:
    `mlx_lm.server` defaults missing-temperature requests to **0.0 (greedy)**, omlx to its
    settings.json **1.0** — and the harness sends no temperature, so the whole item-29/30 comparison
    was unfair (omlx ran stochastic → narration + garbage grep patterns + huge variance). **Replaying
    the captured request at temp 0 made narration vanish.** Fix = a programmatic **temperature-parity
    stamp** in the proxy (input-side, mirrors mlx-lm's default; greedy by default). This refines the
    item-29 verdict: omlx's apparent E2E/agentic deficit was the temp confound, not a tool-call defect —
    item 29's decode-speed ADOPT stands and item 30 adds agentic parity.
  - **Cat-1 (real omlx bugs) fixed programmatically by the repair proxy** (`scripts/omlx_repair_proxy.py`):
    `<eos>`/`<end_of_turn>` leak strip + native/code/keyval text-tool-call → structured `tool_calls`,
    BrokenPipe-safe, with capture + a no-think lever. Failure-mode classifier on the K=5 arm:
    **Cat-1 (A/B/C) = 0, Cat-2 (D narration) = 0** (H/I shared weak-model quality persist, benign —
    all runs still 17/17). 17 unit tests; `make check` + harness selftest green.
  - **Prompt steer UNNECESSARY** (programmatic > prompt, fully): temp-parity alone hits 1.0;
    `omlx-toolsteer.json` kept available but NOT in the default path. Fix is omlx-proxy-only ⇒ mlx-lm
    untouched (no regression).
  - **Prefix cache RE-ENABLED + swept (cache-vs-stability resolved).** Greedy temp-parity removed the
    OOM-driving narration, so the prefix cache is ON by default and **stable** (0 prefill rejections,
    0 evictions, accuracy preserved). 8-arm sweep (`scratchpad/run_30_cache_sweep.sh` +
    `cache_bench.py`/`cache_stats.py`): **~27× warm re-prefill** (cold ~56s → warm ~2s); hot-cache
    SIZE non-critical (SSD-only 27.1× ≈ 4GB 27.4×) → kept small (2GB) for 16GB-fit; **`initial-cache-blocks=512`
    = best lever (+22% → ~33×)**; `memory-guard balanced` no gain (keep safe); MTP coexists (24.8×
    without ≈ 27× with). Tuned defaults give **~35× warm** end-to-end. (Caveat: the cache key is
    model+path-scoped — a stale dir from a different serve path can stall the first turn; consistent
    controller use avoids it.)
  - **Streaming-repair proxy (T3 mechanical fix) + SWE/T3 no-regression DEMONSTRATED.** First pass:
    omlx+fix hung at **step=0** on T3 — root cause was the proxy FORCING non-streaming (to repair the
    full turn), so opencode received nothing until a long T3 generation completed → 600s timeout (NOT
    capability, NOT crash/OOM). Fix: `OMLX_PROXY_STREAM_REPAIR` (default on) passes omlx's SSE through
    incrementally (mirrors mlx-lm's stream-through `_passthrough`: forward upstream status+headers,
    write raw lines), stripping `<eos>` per content delta; text-tool-call repair isn't needed on the
    stream path because temp-parity makes omlx emit native structured `tool_calls`. **Validated:** T2
    still **3/3 (1.0)** via streaming (tool calls intact, no regression); T3 now **engages step 0→10,
    terminates naturally at 509s, FAIL (no-edit)** = the shared capability wall (explores via grep/read,
    can't produce a fix — same regime as mlx-lm's 0/8 T3). So T3 no-regression is now demonstrated, not
    just inferred: omlx+fix engages the hard tier identically to mlx-lm and fails on model capability,
    which a serving fix cannot move.
  - **Deliverables:** hardened `scripts/omlx_repair_proxy.py` + `omlx_repair_proxy_test.py` (17 tests);
    `scripts/omlx.sh` controller + `make omlx-{pull,up,down,status,serve}`; `restart_server`
    generalized via `HARNESS_SERVE_BACKEND={mlx,omlx}`; sweep/measurement tooling under `scratchpad/`.
    Detail: `docs/item24-feasibility-notes.md`.

- **29** — **omlx serving-backend probe — closed 2026-06-29, verdict (i) ADOPT: omlx + VLM-MTP
  speculative decoding (block_size=3) is +37 % over mlx-lm at tool-call parity + lossless output;
  integration deferred.** Where item 24 closed the *model-swap* lever, item 29 attacks the same
  latency wall from the *serving-engine* side with the **model held byte-identical** (no re-quant —
  item 24's closure stands; speculative decoding is lossless so the served model's distribution is
  unchanged). **`jundot/omlx` v0.4.4** (Apache-2.0, brew-vendored, MLX+mlx-lm+mlx-vlm core, continuous
  batching + tiered hot-RAM/cold-SSD KV cache + VLM-MTP speculative decoding).
  - **Stage 1 — cheap fast-exit gate: clean PASS on all four checks.** (a) omlx loaded the
    **byte-identical** pinned safetensors in-place (`gemma4`/`VLMBatchedEngine`, no GGUF/re-quant;
    `model-00002` sha256 == the git-LFS manifest oid); (b) `/v1/models` guard + OpenAI
    `/v1/chat/completions` valid; (c) **native `gemma4` tool-call parser** → clean
    `get_weather{"city":"Paris"}` full round-trip, **no repair proxy** (refutes the README's
    Gemma-not-listed risk flag); (d) **offline under blocked egress** — fresh load + tool-call with
    all proxies/HF poisoned, `lsof` showing only the loopback LISTEN, **zero outbound connections**.
  - **Stage 2 — decode tok/s A/B (slope method, K=5, same weights, proxy-free, serialized
    same-machine).** Plain backend swap: mlx-lm (tuned caps) **11.09** vs omlx `safe` **12.98
    (+17 %)** — below the 20 % bar (would be (ii)). `aggressive≈safe` ⇒ decode is MLX-kernel-bound.
  - **Tuning A/B-1 — tiered prefix cache (`--paged-ssd-cache-dir` + `--hot-cache-max-size` vs
    `--no-cache`):** on a repeated ~3.2 k-token prefix, warm re-prefill **13.38 s → 2.40 s (~5.6×)**,
    `cached_tokens` 2048/2428 restored — directly attacks the item-24 **agentic wall-clock**. (Isolates
    omlx's own cache ON-vs-OFF; not an omlx-vs-mlx-lm head-to-head. On by default.)
  - **Tuning A/B-2 — VLM-MTP speculative decoding (the adopt lever):** matched
    `mlx-community/gemma-4-E4B-it-qat-assistant-bf16` drafter (4-layer MTP head, 17.4 M params,
    tokenizer == target; 6-bit variant crashed on a quantized-embedding reshape, bf16 works). Block-size
    sweep: OFF 12.98 · bs1 ~3.08 (degenerate) · bs2 14.88 · **bs3 15.63 (optimum)** · bs4/default 12.99
    (flat). **bs=3 = +20 % over plain omlx, +37 % over mlx-lm**, **lossless** (target verifies at
    temp 0) and **tool-call parity holds**. Default block_size gives nothing → bs=3 is load-bearing.
  - **Fairness check (the crux of the verdict):** mlx-lm 0.31.3 *has* `--draft-model` but **cannot load
    any available gemma-4 draft** — the only standalone small gemma-4 (`gemma-4-e2b-it-4bit`,
    tokenizer-matched) is multimodal and trips mlx-lm's draft loader (`140 parameters not in model` —
    the **same multimodal-loader wall item 24 hit**, reproduced across `--num-draft-tokens` 2/3/4); MTP
    heads only run via mlx-vlm (omlx's path). So omlx's win rests on its **integrated, working MTP path**
    that mlx-lm lacks here — not a proven raw-kernel gap; the plain swap is only +17 %.
  - **Deliverables:** the byte-identical **serving-backend A/B pattern** + `scratchpad/decode_bench.py`
    + `scratchpad/prefix_cache_bench.py`; adopt config = drafter via `~/.omlx/model_settings.json`
    (`vlm_mtp_enabled`, `vlm_mtp_draft_block_size=3`). **`make omlx-up` + `restart_server`
    generalization + `make check` DEFERRED** (document-now decision). Detail:
    `docs/item24-feasibility-notes.md`.

- **24** — **Small-model survey + model-swap A/B — closed 2026-06-29, verdict (iii):
  no candidate beats the frozen Gemma baseline on this harness; the model-swap lever is
  REJECTED.** The single lever varied was the served model (Gemma-4-E4B QAT → candidate),
  harness/tiers/shaped-reward/sampling held fixed.
  - **24.1 — survey (`docs/small-model-selection-research-v2.md`, [lit-only]).** A
    latest-release refresh retired the v1 shortlist (all superseded/off-budget) and named the
    **Qwen3.5 small dense series** as the A/B shortlist (4B/9B [2026-03-02]). Tool-calling
    literature came back **blank** — every external BFCL/SWE/Aider claim was refuted under
    adversarial verification, so only the local run could rank them.
  - **24.2 — feasibility gate: both PASS.** Both checkpoints serve **text-only** on mlx-lm
    0.31.3 (its `qwen3_5` `sanitize()` drops the vision tower → the survey's `mlx_vlm`-fallback
    risk is retired) through the passthrough proxy with valid native tool-calls (no repair
    shim). Surfaced the **thinking-mode-ON default** (155 vs 6 tokens) → 24.3 runs thinking OFF
    via a new serve-layer `MLX_CHAT_TEMPLATE_ARGS` (recorded covariate). `docs/item24-feasibility-notes.md`.
  - **24.3 — local A/B (the evidence).** **4B = 0.3/11** (spread 0–1; engages + edits,
    made_edit 0.30, timeout-bound 29/33, one real `sympy` fix) · **9B = 0.0/11** (spread 0–0;
    **100% timeout** 33/33, made_edit 0.03 — too slow on the 16 GB M1 to finish an edit) vs the
    **Gemma 0/8** baseline. **Neither clears its spread → (iii).** Two caveats sharpen the
    negative: (1) **quant-method confound** (PTQ candidates vs the QAT baseline) unresolved;
    (2) the Qwen wall is **wall-clock/latency, NOT engagement** — both models engage and edit
    (unlike Gemma's no-tool-stop), so this is a **hardware-bound** negative specific to this
    machine, distinct from Gemma's capability-bound one; a faster host or a higher timeout (off
    the frozen protocol) could move it.
  - **OOM-safe harness deliverable (lasting).** The first 24.3 run **crashed the session** — the
    MLX prompt cache climbed unbounded (3.95→4.60 GB) and Metal-OOM-killed the **4B** (not the
    OOM-flagged 9B); root cause = per-conversation KV-cache accumulation, not model size or
    K-repeat parallelism. Fixed with a `MLX_SERVER_EXTRA_ARGS` passthrough in `mlx.sh`
    (`--prompt-concurrency 1 --prompt-cache-bytes` cap) + a `/v1/models` model-guard (bare
    `mlx.sh up` silently serves the default Gemma) + the `/tmp/py-shim` python3.12 shim. Both
    arms then ran 66 instance-runs to completion with **zero OOM**. Drivers:
    `scripts/.../run_24_3_{4b,9b}_serialized.sh` template.

- **20** — **Planning-first phase / orchestration topology — closed 2026-06-28, verdict
  (ii) PARTIAL: planning-first does not transfer; multi-agent is the only arm that ever
  lands a real T3 fix, but the gain does not survive re-validation.** The [lit-only] 20.1
  survey (full orchestration likely a net loss at 8–12 tok/s; a constrained plan-then-build
  the part worth testing) was **measured** on the local stack. Five topology arms
  (`scripts/harness_configs/plan-*.json`, all on the cand2 `rules_append` base except bare;
  arm b a single-run plan-then-build approximation via APPEND, NOT the `agent.build.prompt`
  REPLACE that item 18 burned; arm c re-enables the globally-disabled `task` tool + planner/
  coder subagents) were A/B'd K=3 on the item-23 6-instance T3 set, scored by the item-23
  shaped reward, + an independent K=3 confirmation re-val of the winner.
  - **20.2 — configs + selftest + feasibility smoke.** 5 arms built, all pass
    `gepa_assert_serving_offline`, no prompt-REPLACE; 6 new selftest checks, `make check`
    green. Smoke (sympy-21614): arm b suppresses tool use (0 valid calls / prose-markdown);
    arm c emits valid calls but **never drives `task`** (degrades to flat churn) — both
    flagged, neither aborted.
  - **20.3 — multi-arm A/B.** bare **0.153** (reused 23.1) · cand2 **0.0** (OOM-regresses —
    measures item-23's unrun "d" arm) · arm a goal+nothink **0.097** · arm b plan-then-build
    **0.083** (within spread of bare — finding #1 "goal plans help" does NOT transfer) ·
    **arm c multi-agent 0.278** (online K=3, Δ+0.125, 22714 flips 3/3 — the correct
    `evaluate`-guard `point.py` fix).
  - **Confirmation re-val (the decisive correction).** Independent K=3: arm c **0.153**
    (=bare), 22714 flips **1/3** (r2 OOM, r3 timeout). **Combined K=6: 0.215, Δ +0.062 ≪
    spread 0.292 → does NOT clear the significance test.** The online win was favorable
    variance.
  - **Decisive finding.** Arm c is the **only** arm that ever fixes a real T3 bug (22714
    **4/6** across K=6; every other arm 0 flips) at **≈bare token cost** (1542 vs 1688 —
    the "multi-agent 8–15×" literature refuted here) — so multi-agent is **not** a uniform
    net loss. **But the mean gain does not survive re-validation, and the win is
    mechanism-incidental:** the `task` tool **never fires** (the weak 4B won't orchestrate —
    confirming the literature/smoke); the gain is a config side-effect (likely the planner/
    coder subagent *descriptions* as goal scaffolding). **Verdict (ii) partial — movable on
    22714, NOT a robust adopt; do not ship arm c as default.** The binding constraint on
    22714 is **OOM/timeout variance, not capability** (correct fix produced 4×) → the next
    lever is the 16 GB / 600 s **resource wall**, not more prompt/topology shaping.
    `make check` green; selftest covers the 5 arm configs + serving-offline + APPEND-only +
    arm-c task/subagent materialisation. Full write-up: `docs/item20-20.3-results.md`.

- **23** — **GEPA on the next rung, T3 (real fixes), via a SHAPED reward — closed
  2026-06-27, verdict (iii): the T3 capability wall holds UNDER SHAPING (validated, not
  assumed).** Item 19 moved the synthetic T2 rung; item 23 pushed GEPA up to **T3**
  (single-file, single-hunk, single-F2P **real** SWE-bench fixes). Binary T3 is a flat
  **0/6** (no gradient), so the precondition was a **dense shaped reward**.
  - **23.1 — machinery shipped + gate UNLOCKED (the lasting deliverable).**
    `gepa_t3_shaped_score` is a TOTAL per-instance reward mapping every terminal to one rung
    (`−0.25` catastrophic/oom/error · `0.0` no-tool-stop · `+0.25` tool-churn · `+0.50` clean
    edit, P2P intact · `+1.0` F2P flip; timeout does NOT cap a clean edit; an F2P-flip-that-
    broke-P2P falls to −0.25). It **replaces item-19's separate λ floor penalty** (floor baked
    into the score). `gepa_t3_fitness` = `T3_shaped_mean` with **T1 AND T2 both hard gates**
    (a sibling of `gepa_fitness`, not a mutation). `gepa_t3_gate_check` uses **two ceilings** —
    unlock the climb on **0.50** (behavioural), report **1.0** (binary F2P flip) as the adopt
    gate; shipped as `gepa-t3-gate`. **T3 corpus expanded 3→6** (mined the offline SWE-bench
    Lite cache → 124 candidates; verified 3 new sympy fixes 22714/18621/15346). Re-baseline
    (K=3): **shaped mean 0.153, headroom-to-0.50 0.347 > spread 0.083 → UNLOCKED** — a genuine
    dense gradient (rungs `−0.25×2 · 0.0×6 · 0.25×7 · 0.50×3`), binary flips 0/6.
  - **23.2 — append channel + 4 seeds.** In the full harness `system_prompt` REPLACES the
    tuned default (the item-18 trap), so added a **`rules_append`** key writing a local
    `AGENTS.md` (opencode APPENDS it) — the full-harness analogue of cand2's `rules.content`
    append. Four terse mode-matched seeds: (a) engage, (b) commit, (c) verify, (d) cand2port.
  - **23.3 — Phase-1 probe → Phase 2 NOT unlocked.** K=3 per arm: **(a) 0.083 (Δ−0.069),
    (b) 0.097 (Δ−0.056) — both REGRESSED** vs baseline 0.153; neither clears spread; (c) K=1
    partial −0.083; **(d) unrun** (the 16 GB M1 Metal-OOM ceiling took the stack + run down 3×).
    Every measured arm **collapsed the one reliable near-miss** (18621 0.50→churn) instead of
    converting headroom → **no candidate clears the 0.50 ceiling.**
  - **Decisive finding (refines items 18/19).** Item 18 = *replacing* the prompt hurts; item 23
    = even *appending* terse, mode-targeted rules **regresses** the weak 4B on **real fixes**
    (it disturbs the fragile near-miss). The shaped reward proved the gradient is real but
    **no text lever converts it** — the T3 wall is capability-bound, now shown under shaping,
    not assumed. Caveat: 2 of 4 arms full (a,b) + c partial; d flagged unrun (a/b/c agree).
    `make check` green; selftests cover totality + two-gate + two-ceiling + `episode_wall_s`
    timing + the `rules_append` append channel.

- **18** — **Improvement-recommender agent — closed 2026-06-26, verdict
  REJECT-the-emitted-config (pipeline validated, top proposal does not transfer).**
  A two-layer data-driven recommender: a deterministic Python **evidence layer**
  (`harness_eval.py recommend` → `build_evidence_digest`) aggregates the on-disk
  episode/ledger corpus by `failure_category` × tier (instance IDs, E0 metric
  signatures, degenerate-loop signal); a **Claude Code agent on Opus 4.8** is the
  **proposer**, consuming that digest + prior-work docs and emitting **ranked
  recommendations**, each materialised as a runnable `harness_configs/*.json` (or a
  flagged `needs-implementation` note). No Jaeger dependency — the durable jsonl corpus
  is the source.
  - **18.0 known-answer backtest — PASS (the pipeline gate).** Over **3 Opus-4.8
    samples** on the baseline pre-fix digest, the proposer scored **recall = 1.0,
    precision = 1.0 on all 3** vs the 7-mode taxonomy (`RECOMMENDER_GROUND_TRUTH` /
    `score_backtest`): it surfaced the known item-16 defects on their instances
    (dropped-output/thinking-stop on 12481/11400/19007, edit gutter/whitespace on
    15345/13043, the 19007 loop) **without over-flagging**. Recommender certified.
  - **18.1/18.2 shipped + schema-gated.** Layer 1 (`build_evidence_digest`,
    `ranked_cells` by `count × headroom × movable`, T3/T4 zeroed) and Layer 2 (proposer
    spec `scripts/recommender_proposer_prompt.md`; `validate_proposal` rejects a
    malformed/non-schema config before it can be A/B'd). `make check` green; selftest
    +11 item-18 checks.
  - **18.3 close-the-loop A/B — verdict REJECT.** The top emitted runnable config
    (`proposed-greedy-toolprotocol`, hash `8cad8a43df03` — greedy temp 0.0 + a terse
    small-model tool-use protocol replacing the long tuned system prompt; targets the
    `no-edit`/dropped-output mode) was A/B'd K=3 vs the baseline-tier K=3 on the identical
    8-instance subset (`b8733c486557`, 600 s cap). **Pass-rate: 0/8 → 0/8** (null, as the
    proposer pre-flagged — T3/T4 capability wall). **But the histogram regressed in the
    wrong direction and tool-call validity broke:** `no-edit` 5→18, `made_edit` 16/24→2/24,
    tool-calls 167→34, dropped-output 2→9 across K=3. **Replacing** the long frontier-tuned
    system prompt with a terse protocol **suppressed tool use** on the weak 4B (it narrates
    instead of editing). The bar (move pass-rate OR shift histogram favourably, *with
    tool-call validity not regressed*) is failed on the disqualifying clause → **the config
    is rejected.**
  - **Decisive finding (refines item 19).** Item 19 found *additive* terse `rules.content`
    helps (T2 0.733→0.917); item 18 shows *replacing* the whole system prompt with a terse
    one **hurts** — the long tuned prompt is load-bearing tool-use scaffolding. "Less-is-more"
    is about *adding less*, NOT *gutting the system prompt*. The recommender pipeline works
    (backtest 3/3); its first emitted lever, like every item-16 mechanical lever, does not
    move the capability wall — and a wrong-direction prompt swap actively regresses the floor.
    Full write-up: `docs/opencode-local.md` (Improvement-recommender section).

- **19** — **Structured prompt-optimisation (GEPA) — closed 2026-06-26, verdict
  ADOPT (modest local win).** With item-16's gate satisfied (the harness is sound,
  the 0/8 is capability-bound), applied a reflective optimiser to the harness text
  levers, with **Opus 4.8 as the in-loop reflector** (item-18 pattern) and the frozen
  local Gemma as optimisee + evaluator (serving offline throughout, guarded by
  `gepa_assert_serving_offline`).
  - **19.2 feasibility gate — UNLOCKED.** Shipped the deterministic fitness/gate core in
    `harness_eval.py` (`gepa_fitness` = `T2_frac − λ·floor_rise`, λ=100, T1 hard gate;
    `gepa_gate_check`; cheap pure-ledger reads; `gepa_budget`; `gepa-gate`/`gepa-score`
    subcommands + `make gepa-gate`). A fresh **K=5** baseline re-measure gave T2 mean
    **0.733**, spread 0.167 → headroom 0.267 > spread → **gate UNLOCKED** (climbable
    signal). Per-rollout 78.5 s; per-candidate ≈23.6 min (K=3).
  - **19.3 GEPA run — ADOPT cand2.** Reflection target = the sole failing T2 check
    (`read_offset_near_grep_line`: model reads >1 line above the grep hit). **cand2**
    (terse, positive-only `rules.content`, 233 ch) lifts **T2 0.733→0.917** (K=6,
    Δ+0.183 > spread; floor 1.6→0.5; T1 held); **cand1** (verbose +numeric example,
    1025 ch) **REGRESSED to 0.278** — the counter-arm. **Decisive finding: prompt
    LENGTH is the dominant lever on this weak 4B model — terseness helps, elaboration
    hurts.** Refines item-16's "prompt changes don't move this harness" (they do, in the
    less-is-more direction). Converged in 2 candidates, no CAPO/OPRO fallback. Offline
    re-validation confirmed the win survives (online K3=1.0, re-val K3=0.833, K6=0.917).
    Adopted: `scripts/harness_micro_configs/gepa-cand2.json`. Caveat: win is on the
    synthetic T2 tool-fidelity rung; **T3/T4 stay 0/8** (capability wall, unmovable by a
    prompt lever). Full write-up: `docs/structured-optimisation-research.md` §19.2–19.3.

- **16** — **Full-harness trace-driven fixes (round 2) — mechanical-lever sweep
  COMPLETE.** The full harness scored **0/8** on the frozen 8-instance sympy subset
  (item-14's micro-suite win did not transfer). Built the measurement floor, swept
  **every** opencode-side / proxy lever L0–L6 under K≥3, and reached a decisive
  conclusion: **no harness-mechanics lever moves SWE 0→>0 — the bottleneck is model
  capability, not the harness** (confirmed independently by item 22's online control).
  - **Enablers (E0/E1/E2/E-sampling).** E1 instance timeout 30→10 min; E2 real-time
    per-episode heartbeat (Popen-streams opencode stderr loop steps + deadline kill);
    E0 episode-metrics instrumentation (`parse_episode_jsonl` + degenerate-loop gradient
    table; reliable activity signal = `tool_call_rounds` from `step_finish.reason`, since
    `tool_use` events are unreliable). E-sampling: verified mlx-lm 0.31.3 honours
    `repetition_penalty`/`repetition_context_size` (NOT `no_repeat_ngram_size`), and the
    wire path end-to-end (opencode's `@ai-sdk/openai-compatible` serialises
    `repetition_penalty` **top-level** onto the request body).
  - **⚑ Methodology finding (shapes every lever A/B).** The tool-call generation path is
    **non-deterministic even at temperature=0 + fixed seed** (MLX/Metal float-kernel
    nondeterminism, below the sampling layer; frozen stack — no knob fixes it). ⇒
    adopt/reject requires **K≥3 runs/config**, mean delta clearing the run-to-run spread.
    `harness_eval.py run --repeats K` + a "K-run aggregates" summary table.
  - **Tiered baseline gradient.** Micro (T1/T2) ≈ ceiling (T1 4/4, T2 ~4–6/6, T3 4/4);
    SWE (T3/T4) **0/8** (K=3, spread 0–0). The cliff is exactly synthetic→real (T2→T3) — a
    capability wall; dominant SWE mode is `tests-failed` (real edits, wrong fix).
  - **Lever verdicts (L0–L6 — none move SWE 0→>0):** **L0** baseline 0/8. **L1**
    anti-repetition — REJECTED as a pass-mover (Δ inside spread; safe, holds ceiling;
    wire-verified). **L3** edit-application — two real bugs FIXED (L3a diff-vs-`base_commit`
    so committed fixes aren't mis-scored no-edit; L3b `.opencode/tools/edit.ts` forgiving
    matcher) = correct *insurance*, target defects intermittent. **L5** doom_loop —
    REJECTED (SWE timeout 7→7 unchanged; opencode's detector fires on *identical* repeated
    calls, but this stack's timeouts are varied churn / one long slow generation — wrong
    detector; micro no-regression 1.0). **L6** no-think (`MLX_PROXY_NO_THINK=1`) —
    CONDITIONAL, not adopted (micro K=6 broke its perfect ceiling; SWE regression check
    found real-edit-attempts 12→4, +10% wall-clock — helps executor turns, **hurts
    reasoning-dependent fixes**; needs per-turn gating the frozen stack lacks). **L2**/**L4**
    never triggered → not built. Configs in `scripts/harness_configs/` +
    `scripts/harness_micro_configs/`.
  - **Conclusion:** harness floor solid; every lever has a documented adopt/reject; the
    binding constraint is capability on real fixes. The only tier with headroom is the
    **micro gradient (T1/T2)** — the cheap fitness signal for item 19 (GEPA), now
    **UNBLOCKED**. Docs: `docs/opencode-local.md`, `docs/harness-engineering-research.md`.

- **22** — **Online-model harness-soundness control (diagnostic for item 16).**
  Ran the **exact same full harness** (frozen 8-instance tier≥3 sympy subset, same
  tools/prompt/scoring) against a strong online model — `opencode/big-pickle`, the
  free hosted model on the opencode zen gateway — to isolate **harness mechanical
  bugs from local-model capability**. Diagnostic/CI control only; the frozen local
  serve stack is unchanged.
  - **22.1/22.2** — Added an `external_provider` gate that short-circuits ALL THREE
    local-only assumptions so the run works with **MLX fully off**: `apply_levers`
    writes no `mlx-local`/`baseURL` block (sampling/limit ride opencode's built-in
    provider), `cmd_run` skips `server_healthy`/restart/`detect_model`, and
    `score_instance`/`_score_subset` skip the local OOM probe/restart (which would
    have mislabelled every online timeout as `oom`). An `online_preflight` auth+network
    check replaces the MLX health-check; selftest asserts no local leak + pinned ref.
    `harness_configs/online-bigpickle.json` lever config + `make harness-eval-online`
    (no `mlx-up` dep) make it one command. `make check` green.
  - **22.3/22.5** — **VERDICT: HARNESS SOUND.** BigPickle scored **4/8** (`ok`) on the
    identical subset. The aggregate sits in the numeric "inconclusive" band, but the
    pre-registered 22.5 disambiguation resolves it on the **histogram** (the primary
    evidence): re-running the 4 failures at the Gemma-identical 600s cap collapses the
    failure modes to **100% capability modes** with **ZERO mechanical/harness modes**:

    | arm (same subset `b8733c486557`) | pass | failure histogram |
    |---|---|---|
    | Gemma-4-E4B baseline (K=3 mean) | **0/8** | `tests-failed`, `timeout`, `no-edit` — **never one `ok`** |
    | BigPickle @240s (22.3) | **4/8** | `ok`×4, `timeout`×2, `catastrophic-edit`×1, `no-edit`×1 |
    | BigPickle @600s (22.5, Gemma-identical) | **4/8** | `ok`×4, `tests-failed`×3, `catastrophic-edit`×1 — **0 oom / 0 degenerate-loop / 0 no-edit / 0 edit-mismatch** |

    Trace reading confirmed the pipeline end-to-end: a PASS (sympy-15345) captured a
    real `_print_Max/_print_Min` fix → 10 tests passed; the 22.3 `no-edit` (sympy-19007)
    was a genuine `length` output-budget cutoff (grep/read only, zero edit attempts),
    not a harness miss — at 600s it completes with a real edit and **F2P 1/3 partial**,
    proving the scorer reads actual pytest results, not a binary mis-score. The 22.3
    `timeout`/`no-edit` categories were artifacts of a deliberately-tightened 240s cap;
    at the Gemma-identical 600s they vanish. **Decisive contrast:** Gemma never writes a
    single correct fix (0 `ok` across 3 repeats) while BigPickle writes 4 on the
    **identical** scaffolding — so the harness demonstrably *can* score passes and the
    local 0/8 is genuinely **capability-bound**, not harness-broken. Item-16's
    capability-bound framing holds; GEPA/prompt work is unblocked. One-shot control —
    re-run only after structural harness changes. Docs: `docs/opencode-local.md`.

- **17** — **Tiered validation harness.** Replaced the binary "micro-passes /
  full-harness all-fails" signal with a 4-tier gradient + failure-mode breakdown.
  Unified both harnesses into one ladder (**T1** micro single tool-call · **T2**
  micro multi-step + micro-edit · **T3** single-file real fix · **T4**
  multi-file/multi-site real fix; `GLOBAL_TIERS`/`MICRO_TIER_MAP`). Per-test static
  metadata (`tier`, `n_files`, `needs_search`, `needs_bash`, `expected_tool_seq`)
  plus a **per-episode-derived `failure_category`** mapping `reason` + E0 metrics to
  the item-16 7-mode taxonomy (shared vocabulary with items 16/18). Offline `tier`
  subcommand buckets instances from cached gold patch + F2P set (frozen sympy-8:
  T3=3, T4=5). `tier_breakdown()`/`build_tier_report()` give per-config per-tier
  pass/total + failure histogram; `_render_tier_report` folds into `summary.md` and
  `write_tier_report` emits `tier-report.jsonl` (pure aggregation, no re-run) — a
  cheap fitness signal for item 19. `make check` green; both selftests OK.
  Docs: `docs/tiered-harness.md`.
- **21** — **Sandboxed code-execution ("code mode") for parallel/chained tool
  calls.** Investigated driving tool calls through a code-execution sandbox so the
  agent batches/chains/parallelises N calls in one rollout instead of one
  tool-call-per-decode-pass (the dominant wall-clock cost at 8–12 tok/s).
  - **21.1** deep-research survey (18 sources): mechanism sound, **Monty = Pydantic
    Monty** deployable offline (~5 MB, in-process, MIT) but alpha; lit claimed a
    weak-model "structure tax". `docs/sandbox-codeexec-research.md`.
  - **21.2a/b** decisive **local code-gen gate — PASSED**: Gemma-4-E4B
    orchestration-code pass@1 **1.0** across base (18/18) + hard (25/25) tiers, under
    both restricted `exec` and the real `pydantic_monty` v0.0.18 engine — **locally
    refutes the "structure tax"** (markdown code blocks, not JSON-wrapped). Monty's
    alpha dialect taxed the *frontier* control, not Gemma (which writes plainer
    loops). `scripts/codegen_probe.py`.
  - **21.3** round-trip A/B prototype (mock harness): code-mode vs flat ReAct =
    **−83% wall-clock · −91% tokens · +0.667 pass@1** (ReAct non-terminated on 4/6 —
    item-16 pathology). `scripts/codemode_ab.py`.
  - **21.4a** shipped the real executor `scripts/codemode_exec.py` (sandbox bound to
    real host-tools, path-jailed, JSON envelope) + `.opencode/tools/codemode.ts`;
    **local Gemma invoked `codemode` natively** end-to-end through the live agent loop
    + repair proxy (one decode, 9 host-ops).
  - **21.4b** production A/B on real opencode: the 21.3 5× is **TEMPERED** — real
    opencode has `bash` (itself a "code mode"), so the model self-batches and
    codemode's edge shrinks to ~24%; it still clearly wins non-self-batched cases
    (def_count grep×4 → 2 calls, −56% wall) and never lost, but does **not** fix the
    degenerate-loop. **codemode kept enabled**; cite 21.3 as a bash-less upper bound.
  - **21.4c** firmed up at **k=5** on **bash-hostile** tasks (multi-step parse,
    conditional aggregation, cross-file reasoning) vs the same bash-equipped baseline
    — `scripts/codemode_niche_ab.py`, ledger `codemode-niche-ab.jsonl`, 4 tasks ×
    k=5 × 2 arms = 40 episodes at the 600s Gemma cap. **Code-mode's real niche is
    confirmed — but it is a RELIABILITY/LATENCY win, not a correctness win.** Overall
    (20 episodes/arm): **termination 1.00 vs 0.80** (baseline timed out at 600s on
    20%; codemode never did), **wall-clock 150.6s vs 286.9s (~1.9× faster**, ~2.2–2.9×
    on the two timeout-prone tasks), **round-trips 1.55 vs 2.65 calls** — yet
    **correctness REGRESSED, 0.55 vs 0.80**. Per-task the separation is clean and
    mechanistic: on `const_sum`/`add_docstring_count` the baseline can't express the
    parse as a shell one-liner, falls into grep/read churn, and **times out 40%** of
    the time, while codemode single-shots it (ok 1.0, ~3× faster); on `orphan_count`
    the baseline's grep-churn (7.6 calls) lands the right answer **5/5** where codemode
    collapses to 2 calls but is **1/5 correct** (the weak Gemma writes buggy
    orchestration code — e.g. `name.isalpha()` rejecting underscore constants); on
    `sentinel_digit_sum` (a single-call task) the arms are **identical** and the model
    **doesn't even invoke codemode** (used `grep`). Two enabling facts surfaced: the
    sandbox is **builtins-only** (the model reaches for `import re` and the call dies —
    a `no-import` nudge is required for code-mode to work on parse tasks), and the
    bash-equipped baseline **never actually used `bash` (0%)** on these tasks — even
    when available, the weak model defaults to grep/read round-trips when there's no
    clean shell one-liner, which refines 21.4b: `bash` only tempers code-mode where a
    one-liner exists. **Verdict — ADOPT (keep `codemode` enabled/available):** it is a
    net-positive, never-times-out tool that ~3×-speeds and de-churns genuinely
    multi-step tasks, and the model selects it ~75% of the time on those. **But do NOT
    add a default-on global nudge steering the model into it:** on the frozen
    capability-bound model it converts churn-to-timeout into fast-but-wrong (the
    bottleneck shifts from round-trips to code quality — the same item-16 wall), so a
    forced default trades correctness for speed. Revisit the default-on question only
    if model capability moves (items 16/19). New `codemode_niche_ab.py` is ruff- +
    mypy-clean and its offline `selftest` passes (pre-existing ruff/mypy debt in the
    sibling `codegen_probe.py`/`codemode_ab.py`/`codemode_prod_ab.py` is untouched and
    out of scope). Doc: `docs/codemode-setup.md`.
