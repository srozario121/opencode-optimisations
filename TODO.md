# TODO — opencode-optimisations

The repo's running work-ledger. **Items 20 and 23** are the open work. **Completed
items 1–15, 16, 17, 18, 19, 21, and 22 now live in `CHANGELOG.md`** (items 18 and 19's
full ticked detail is also kept inline below for reference). Item 16 (the dominant harness
bottleneck) closed 2026-06-25: the L0–L6 mechanical-lever sweep is complete and the 0/8
is **capability-bound, not a harness defect**. **Item 19 (GEPA) closed 2026-06-26: ADOPT
cand2 (terse rules, T2 0.733→0.917) — prompt length is the dominant lever on this weak 4B
model.** **Item 18 (recommender) closed 2026-06-26: the two-layer pipeline is validated
(18.0 backtest 3/3 recall=precision=1.0), but its top emitted config REGRESSED in the
decisive 18.3 A/B (no-edit 5→18, made_edit 16→2, tool-calls 167→34) → verdict REJECT —
replacing the long tuned system prompt with a terse one suppresses tool use; refines item
19's "terse helps" to "adding less helps, gutting the prompt hurts".** **Item 23 (new)
follows item 19: push GEPA up to the next rung, T3 (single-file real fixes), via a shaped
reward + a longer run** — the 3 T3 instances fail in 3 distinct modes (no-tool-stop /
tool-churn / near-miss), one of which (21614) is a clean near-miss.

> **Fixed constraints (carry-through from items 8–11, non-negotiable for every
> open item below).** Fully local / offline at serve time; **16 GB M1**
> (~8–12 tok/s decode, ~40–50K-token Metal-OOM ceiling); single-user interactive
> opencode against the local MLX `/v1` endpoint; **model + serving engine are
> FROZEN** (Gemma 4 E4B QAT on mlx-lm 0.31.3). Only **opencode-side / harness
> levers** are in scope. Tool-call reliability is a hard floor — the repair proxy
> (`scripts/mlx_repair_proxy.py`) stays ON for all runs. See
> `docs/harness-engineering-research.md` for the ranked lever survey (L1–L7).

> **Evidence policy (non-negotiable).** Literature / deep-research findings are a
> **starting point, never a conclusion.** Every claim a decision rests on — whether
> it argues **for** a lever ("planning helps", "GEPA helps") or **against** one
> ("multi-agent is a net loss", "full-thinking hurts") — must be **validated on THIS
> machine** (the local harness: Gemma-4-E4B / opencode / MLX on the 16 GB M1) before
> it is **adopted OR rejected**. Negative claims get a **counter-arm** — build the
> minimal version and measure it; never drop a lever on papers alone. A research doc
> may *rank* a lever; only a local-harness run may *close* it. Tag any
> not-yet-validated finding **[lit-only]**.

---

## Open

### 18. Improvement-recommender agent  ✅ CLOSED 2026-06-26 → `CHANGELOG.md`  (was drafted as "13")

> **CLOSED — verdict REJECT-the-emitted-config (pipeline validated).** The two-layer
> recommender is built and certified (18.0 backtest 3/3 recall=precision=1.0), but its
> top emitted config (`proposed-greedy-toolprotocol`) REGRESSED in the decisive 18.3
> local A/B: pass-rate 0/8→0/8, but `no-edit` 5→18, `made_edit` 16→2, tool-calls 167→34
> (K=3) — replacing the long tuned system prompt with a terse one suppresses tool use.
> Full detail kept below (ticked) + recorded in `CHANGELOG.md`.

**Goal.** A data-driven recommender with a **two-layer split**: a deterministic
Python **evidence layer** reads the **already-captured local episode corpus**
(per-episode `opencode.jsonl` NDJSON + the `ledger.jsonl` E0 metric blocks + the
item-17 `tier-report.jsonl`) and emits a structured digest; a **Claude Code agent
running Opus 4.8** is the **proposer** — it consumes that digest plus the prior-work
docs and reasons out **ranked harness improvements**, each materialised as a runnable
item-17 lever config so it can be A/B'd directly. Item 16 already proved trace-review
*by hand* finds the real defects (L3a patch-capture, L6 thinking-stop, L3b
edit-matcher, L5 loop) — this item **automates that diagnostic loop** (a deterministic
digest feeding an Opus-4.8 reasoner) and is validated by whether the proposer
**rediscovers those known defects** and whether its proposals **move a
tier/failure-mode** under item 17.

> **Not gated behind item 16.** This is *analysis over existing artifacts*, not a
> new harness lever — unlike 19/20/21.4c it does not need item-16's pass-rate to
> move first. It can run **now** against the 80+ episode jsonl files already on
> disk (`runs/baseline-L0-*`, `nothink-*`, `l3-measure-r{1,2,3}`, `*-tier-r*`).
> **Evidence policy still binds its OUTPUT:** every recommendation the Opus-4.8
> proposer emits is a *hypothesis* tagged **[lit-only/tool-proposed]** until a local
> K≥3 A/B (18.3) closes it — the proposer ranks, only a harness run adopts.

### Design decisions (resolved — plan-review 2026-06-24)

Settled from a repo audit (sources verified to exist + be queryable). The three
build/validation decisions were **user-confirmed 2026-06-24**.

- **Input source → the on-disk episode corpus, NOT Jaeger.** Verified: every
  episode already persists its full `--format json` NDJSON to
  `~/.config/opencode-optimisations/harness-eval/runs/<run>/<instance>/opencode.jsonl`
  (80+ files present now), and `parse_episode_jsonl` (`harness_eval.py:586`)
  already structures exactly what 18.1 asks for — tool-call rounds, `errored_tools`,
  `dropped_output`, `made_edit`, `steps_to_first_edit`, `first_tool_offset_s` (latency),
  `max_line_repeat`/`degenerate_loop`. The `ledger.jsonl` rows carry these +
  `failure_category` per instance; `tier-report.jsonl` carries the per-tier ×
  failure-mode histogram. **Jaeger/OTel is real but the WRONG source here:** Jaeger
  all-in-one is in-memory only (cleared on stop), requires bringing the stack up with
  `MLX_OTEL=1` + a sourced env, is best-effort/ephemeral, and its spans carry no
  per-token text for degenerate-loop detection. **⇒ 18.1 ingests the durable local
  jsonl/ledger corpus; the original "Jaeger traces" framing is dropped.** (Jaeger
  stays a live human debugging aid, documented in `docs/jaeger-tracing.md`.)
- **claude-mem is NOT a programmatic input.** No `.claude-mem/` store exists in this
  repo; "observations" are the Claude Code auto-memory (mem-search), not a queryable
  JSONL. The recommender's *prior-work* context = the `docs/*-research.md` files +
  this `TODO.md` history, read as text — not a claude-mem feed. (Removed from 18.1.)
- **Failure vocabulary → reuse the shipped shared taxonomy.** No new enum: **Layer 1**
  classifies via `classify_failure` / `FAILURE_CATEGORIES` (the item-16 7-mode +
  3-outcome set already in `harness_eval.py`), so the digest and every recommendation
  speak item-17's language.
- **Output surface → a ranked report WHERE EACH ITEM EMITS A RUNNABLE CONFIG.**
  Free-text alone is rejected. Each recommendation = `{failure_mode, evidence
  (instance IDs + metric deltas), proposed lever, emitted harness_configs/*.json or
  harness_micro_configs/*.json}`, so 18.3 can run it through `harness_eval.py run`
  with zero hand-translation. The config schema is the existing one (`sampling`,
  `opencode_config`, `env`, `system_prompt`). *(user-confirmed 2026-06-24)*
- **Lever concreteness → ALWAYS emit a runnable config; code-requiring levers are
  flagged, not auto-emitted.** Recommendations are **restricted to levers
  expressible in the existing config schema** (`sampling` / `opencode_config` /
  `env` / `system_prompt`) so 18.3 stays fully push-button. A diagnosed defect whose
  only fix needs **new code** (e.g. a new `.opencode/tools/*.ts` shadow like L3b, or
  a proxy change like L6) is surfaced as a separate **`needs-implementation` note**
  (mode + evidence + target seam) — explicitly NOT a runnable config, so it never
  enters the automatic A/B path until a human/agent implements it. *(user-confirmed 2026-06-24)*
- **Build form → a two-layer split: a deterministic `harness_eval.py recommend`
  evidence layer + a Claude Code (Opus 4.8) proposer agent.** *(user-revised
  2026-06-24 — the proposing agent is now driven through Claude Code on Opus 4.8.)*
  - **Layer 1 — evidence digest (deterministic, Python).** A `harness_eval.py
    recommend` subcommand reuses the existing argparse parser, `parse_episode_jsonl`,
    `classify_failure` / `FAILURE_CATEGORIES`, and the ledger reader to aggregate the
    on-disk corpus into a structured **evidence digest** (per `failure_category` ×
    tier: instance IDs, metric deltas, degenerate-loop signal). This layer is offline,
    unit-tested, and under `make check` / `selftest`. It does **not** itself rank or
    invent levers — it produces the grounded evidence the proposer reasons over.
  - **Layer 2 — proposer (Claude Code, Opus 4.8).** A Claude Code agent on **Opus 4.8**
    consumes the Layer-1 digest + the prior-work docs (`docs/*-research.md`, this
    `TODO.md` history) and emits the **ranked recommendations**, each as a runnable
    `harness_configs/*.json` (or a flagged `needs-implementation` note). The LLM does
    the open-ended diagnostic reasoning a fixed heuristic can't; the deterministic
    digest keeps it grounded in real metrics, and the 18.0 backtest + 18.3 A/B keep its
    output honest. **This supersedes the earlier "no `.claude/agents/` LLM agent"
    decision** — the user has chosen the LLM proposer (validated, not unit-tested).
  - Rejected: a standalone `scripts/recommend.py` (duplicates plumbing); a *purely*
    deterministic ranker with no LLM (can't surface novel/cross-mode levers).
- **Validation gate → known-answer backtest scored on RECALL *and* PRECISION vs the
  7-mode taxonomy (primary), plus 18.3 close-the-loop (decisive).** Build a
  **labelled ground-truth set** from the pre-fix corpus (`baseline-L0-*`,
  `nothink-*`): each known item-16 defect tagged to its instance(s) —
  dropped-output/thinking-stop on 12481/11400/19007, edit gutter/whitespace on
  15345/13043, the 19007 364-round loop. The **Opus-4.8 proposer passes only if** it
  (a) surfaces those true modes on their instances (**recall**) **AND** (b) does not
  over-flag — spurious recommendations are penalised against the taxonomy
  (**precision**), so a recommender that flags everything **fails**. Because the
  proposer is an LLM (non-deterministic), run the backtest **over a few proposer
  samples** and require the recall/precision bar to hold on the **majority** (report
  per-run spread, not a single draw — mirrors the item-16 K-run discipline). The
  **decisive** gate remains 18.3: ≥1 emitted config, A/B'd at K≥3, moves a tier or
  failure-mode vs baseline. *(user-confirmed 2026-06-24; proposer = Opus 4.8 per
  user-revision 2026-06-24)*

- [x] **18.1 Episode-corpus ingestion** (`harness_eval.py recommend`, part 1) — **DONE**
      (2026-06-25). `build_evidence_digest` loads the on-disk `ledger.jsonl` (over the
      per-episode `opencode.jsonl` E0 metrics + `tier-report.jsonl` vocabulary) and
      aggregates by `failure_category` × tier: per mode a count, distinct instance IDs,
      the tiers it hits, and an E0 **metric signature** (mean steps / steps-to-first-edit
      / output-tokens / tool-call-rounds; made_edit/degenerate-loop/dropped-output/
      timed-out rates; common errored tools); per tier pass-rate + headroom + a `movable`
      flag. `parse_episode_jsonl` + `classify_failure` + `instance_tier` reused verbatim.
      `ranked_cells` order by `count × headroom × movable` (T3/T4 `movable:false` ⇒ zeroed).
      **No Jaeger dependency.** Verified live over the 19-row swebench corpus.
- [x] **18.2 Recommendation surface (Claude Code / Opus 4.8 proposer)** — **DONE**
      (2026-06-25). Proposer spec at `scripts/recommender_proposer_prompt.md`; exercised
      as 3 Opus-4.8 Claude Code agents over the baseline digest → ranked recommendations,
      each tying a mode to evidence (instance IDs + metric deltas) + a proposed lever.
      Schema-expressible levers materialise as runnable `harness_configs/*.json` (e.g. the
      emitted `proposed-greedy-toolprotocol.json`); code-requiring fixes (edit-matcher,
      repair-proxy) emit flagged `needs-implementation` notes with a `target_seam`. The
      `recommend --validate` gate (`validate_proposal`/`validate_proposed_config`) rejects
      a malformed/non-schema config before it can be A/B'd; a worked output is committed at
      `scripts/recommender_sample_proposal.json`. Original spec retained below:
      feed the
      Layer-1 digest to a **Claude Code agent on Opus 4.8**, which emits the **ranked**
      report; each item ties a failure mode (shared taxonomy) to **evidence (instance
      IDs + metric deltas)** and a **proposed lever**. **A lever expressible in the
      existing config schema (`sampling` / `opencode_config` / `env` / `system_prompt`)
      is materialised as a runnable `harness_configs/*.json` /
      `harness_micro_configs/*.json`**; a defect needing **new code** is emitted as a
      flagged **`needs-implementation` note** (mode + evidence + target seam), NOT a
      runnable config. The proposer is prompted to rank by `(mode frequency × tier
      headroom)`, prioritising the only tiers with a movable signal (T1/T2), consistent
      with the item-16/19 "T3/T4 is a capability wall" finding. The agent's emitted
      configs are **schema-validated** before they count (reuse the `apply_levers` /
      config-load path) so a malformed LLM output is rejected, not silently A/B'd.
- [x] **18.3 Close the loop (the decisive validation)** — **DONE → VERDICT REJECT
      (2026-06-26).** Ran `harness_eval.py run --config proposed-greedy-toolprotocol
      --repeats 3` (label `item18-ab-greedytool`, hash `8cad8a43df03`) on the local
      Gemma/MLX stack against the identical frozen 8-instance subset (`b8733c486557`,
      600 s cap), vs the `baseline-tier-r1..r3` K=3 arm. **Pass-rate 0/8 → 0/8** (null,
      spread 0 — exactly the tripwire-on-the-T3/T4-capability-wall null the proposer
      pre-flagged). **But the histogram regressed in the WRONG direction and tool-call
      validity broke:** over K=3, `no-edit` 5→**18**, `made_edit` 16/24→**2/24**,
      tool-calls 167→**34**, dropped-output 2→**9**, `tests-failed` 12→**1**. **Replacing**
      the long frontier-tuned system prompt with a terse 4-sentence protocol (+ greedy temp
      0.0) **suppressed tool use** on the weak 4B — it narrates instead of editing. The bar
      (move pass-rate OR shift histogram favourably, **with tool-call validity not
      regressed**) fails on the disqualifying clause → **config REJECTED.** Refines item
      19: *additive* terse rules help (T2 0.733→0.917), but *gutting* the system prompt for
      a terse one hurts — the long tuned prompt is load-bearing tool-use scaffolding.
      The recommender PIPELINE is validated (18.0 backtest 3/3); its first emitted lever,
      like every item-16 mechanical lever, does not move the capability wall — and a
      wrong-direction prompt swap actively regresses the floor. (Closed per Evidence
      policy: a local A/B closes the [tool-proposed] candidate; the verdict is REJECT.)
- [x] **18.0 (validation prereq) Known-answer backtest — RECALL *and* PRECISION.** —
      **DONE → PASS (2026-06-25).** Ground truth `RECOMMENDER_GROUND_TRUTH` tags each known
      item-16 defect to its instance(s): dropped-output/thinking-stop → `no-edit` on
      12481/11400/19007, edit gutter/whitespace → `edit-mismatch` on 15345/13043, the
      19007 364-round loop → `degenerate-loop`. `score_backtest` scores the proposer's
      (mode, instance) claims for recall + precision; over **3 Opus-4.8 samples** on the
      baseline pre-fix digest, **all 3 scored recall = 1.0, precision = 1.0** (majority
      bar cleared, zero over-flagging). Run via
      `harness_eval.py recommend --backtest <sample>.json …`. Recommender certified.
- [x] **`make check` (ruff + mypy + pytest) green** for the **Layer-1** `recommend`
      digest — **DONE.** `harness_eval.py` stays ruff+mypy clean; selftest adds 11 item-18
      checks covering the digest aggregation (synthetic 2-suite ledger), config
      **schema-validation** incl. the `needs-implementation` split + the null-tolerance,
      the whole-proposal gate, and the backtest scorer (recall/precision). The **Opus-4.8
      proposer (Layer 2) is validated by the 18.0 backtest, not unit tests** — its quality
      gate is recall/precision over several samples, not a fixed assertion. *(Note:
      pre-existing ruff/mypy red in item-21 files `codegen_probe.py`/`codemode_*.py` is
      unrelated to item 18 and untouched.)*

### Measurement plan (item 18)

- **Baseline / corpus:** the existing on-disk episode runs (no new **local Gemma /
  MLX** serving run needed for 18.0–18.2 — Layer 1 is offline aggregation over
  artifacts already on disk). **Layer 2 calls Opus 4.8 via Claude Code**; like item
  19's cloud reflector this lives in the *analysis/optimisation* loop, **not the
  frozen offline serve path**, so it does not touch the local-at-serve constraint.
  Only 18.3's A/B re-runs the local model.
- **The single thing 18 produces:** a ranked, evidence-backed, config-emitting report.
- **Signal that the PROPOSER works (18.0):** **recall AND precision** vs the 7-mode
  taxonomy on a labelled pre-fix-corpus ground-truth set — the Opus-4.8 proposer must
  surface the known item-16 defects on their instances *and* not over-flag (flagging
  everything fails), scored over several samples on a majority bar.
- **Signal that a RECOMMENDATION works (18.3):** the emitted config, A/B'd at K≥3 via
  `harness_eval.py run` + `report`, moves a tier pass-rate or shifts a failure-mode
  histogram vs baseline, clearing the K-run spread, **with tool-call validity not
  regressed**.
- **Gate:** `make check` green for any code touched.

### Documentation (item 18)

- [x] **Update** `docs/opencode-local.md` (master doc) — **DONE.** New *Improvement-
      recommender (TODO item 18)* section: the two-layer design, the durable jsonl input
      corpus (NOT Jaeger), the two gates (schema-validate + backtest), the 3/3
      recall=precision=1.0 validation result, and the commands.
- [x] **Update** `docs/tiered-harness.md` — **DONE.** Added *The recommender consumes this
      report* + the `recommend` command; documents reuse of `classify_failure`/
      `instance_tier` and the `movable`-zeroed T3/T4 priority hint.
- [x] **Update** `docs/jaeger-tracing.md` — **DONE.** Added a callout that Jaeger is a
      live human-debugging aid and the recommender uses the durable jsonl corpus instead.
- [x] **Update** `CHANGELOG.md` — **DONE (2026-06-26).** Item 18 closed entry recorded under
      *Done (items 16, 17, 18, 19, 21, 22)*: the two-layer pipeline + the 18.0 backtest 3/3
      PASS + the **18.3 REJECT** verdict (config regressed: no-edit 5→18, made_edit 16→2,
      tool-calls 167→34) and the item-19 refinement ("adding less helps, gutting the prompt
      hurts").

### 19. Structured prompt-optimisation (GEPA)  ✅ CLOSED 2026-06-26 → `CHANGELOG.md`

> **CLOSED — verdict ADOPT (modest local win).** GEPA's cand2 (terse, positive-only
> rules) lifts **T2 0.733→0.917** (K=6); cand1 (verbose) regressed to 0.278 → **prompt
> LENGTH is the dominant lever on this weak 4B model**. Full detail kept below (ticked)
> + recorded in `CHANGELOG.md` and `docs/structured-optimisation-research.md` §19.2–19.3.

**Goal.** Apply a structured optimiser to the harness's text levers (system/agent
prompts, tool descriptions, skill docs). **Item-16 gate SATISFIED** (closed
2026-06-25): the L0–L6 lever sweep is complete and item 22's online control proved the
harness sound, so the full harness gives a non-degenerate signal and the 0/8 is
capability-bound. **19.2 gate UNLOCKED** (K=5 T2 climbable) → **19.3 ran → ADOPT cand2.**

> **✅ PRECONDITION MET (both of 2 done, 2026-06-26) — 19.3 is UNBLOCKED.**
> Blocker **(1)** — item-16 **L5** adopt/reject verdict — **MET** (L5 `doom_loop`
> REJECTED, see `CHANGELOG.md`; the whole L0–L6 sweep is closed). Blocker **(2)** — the
> **T2 gate-check** — now **MET**: a fresh **K=5** baseline re-measure gives **T2_mean
> 0.733, spread 0.167, headroom 0.267**; the unlock rule `(1−mean) > spread`
> (`0.267 > 0.167`) **PASSES → GATE UNLOCKED** (`docs/structured-optimisation-research.md`,
> §19.2). The T2 micro rung shows a real, non-saturated, above-noise gradient. *(Caveat:
> the unlock is modest — headroom exceeds spread by only ~0.1, < one instance on the
> 6-instance rung — and budget is the binding constraint: per-candidate ≈ 23.6 min at
> K=3, so a meaningful N≈10 run needs ~4 h awake compute → 19.3 runs small-N with
> abort→fallback.)*
> Rationale: item-16's evidence is a **stable 0/8 T3/T4 capability wall** (not harness
> mechanics), and the only tier with real headroom is the synthetic **T2** rung — so GEPA
> only has somewhere to climb if T2 still shows a non-saturated, non-noise gradient. It does.

### Design decisions (resolved — plan-review 2026-06-24)

- **Fitness signal** → `score = T2_frac − λ·(tool_call_regression)`, read cheaply
  from item-17's `tier-report.jsonl` (pure aggregation, no re-run). **T2-only is the
  climbing signal** (the one tier with headroom). **T1 is a HARD GATE** — if a
  candidate drops T1 below baseline it is **rejected outright** (not soft-penalised).
  **T3/T4 are reported but weight 0** (stable 0/8 → no gradient, would only add noise).
- **Penalty term** → `tool_call_regression` = the net **rise above baseline** in
  `no-edit + error + catastrophic-edit` counts (the item-17 shared taxonomy —
  "asked-for call never landed" + runtime error + "edit broke working code"). **λ is
  set LARGE** — large enough that **any** net floor regression drives the score
  **negative vs baseline**: a T2 gain can **never buy back** a tool-call regression.
  The floor is near-absolute, consistent with the T1 hard gate.
- **Climbable-gradient threshold (gate-check unlock rule)** → unlock GEPA **only if**
  T2 mean (K≥3) is strictly inside `(floor, ceiling)` **AND** remaining headroom
  exceeds the run-to-run spread: **`(1.0 − T2_mean) > K-run spread`**. If the headroom
  to ceiling is smaller than the sampling noise, GEPA cannot prove a gain on this
  stack → stays gated, record "no climbable signal yet".
- **Reflector / proposer** → a **larger/cloud model MAY be the reflector/proposer
  ONLY**; the **frozen local Gemma stays the optimisee + the model the harness
  evaluates**. **Serving stays offline; the optimisation loop may be online.** The
  reflector runs only in the offline-optional loop, consumes **captured local rollout
  traces**, and emits **only text levers** (prompt / tool-desc / skill-doc strings)
  written into the config bundle (`system_prompt`→`AGENTS.md`, tool descriptions,
  skill docs via `apply_levers`). It is **never in the serve path** and never sees a
  live request — assert "serving-offline" on every run.
- **Offline re-validation (mandatory before adopt)** → the final adopted candidate
  must be re-validated in a **fully-offline rerun with the reflector disconnected**.
  It counts as "the win survives" iff the offline T2 score stays **within the K-run
  spread** of the online-adopted score **AND** holds the T1 hard-gate + non-regressed
  floor. **The adopted text must stand alone without the reflector present.**
- **Counter-arm (validates the NEGATIVE claim, per Evidence policy)** → a **single
  fixed GEPA-proposed candidate vs the frozen baseline, K≥3** — the minimal "does
  optimisation move it at all" arm. If even one GEPA candidate can't clear the spread,
  item-16's "prompt/skill changes don't move this harness" finding holds under a
  controlled run (not just hand trace-review).
- **Budget (tier-scoped)** → **T2-only**. Cap = **≤N candidates × K=3 rollouts** on
  the T2 subset, with a **wall-clock ceiling** computed in 19.2 from the measured
  per-T2-rollout time; **abort → fallback if unconverged**. **Do NOT attempt a T3/T4
  GEPA run until the capability wall moves.**
- **Fallback** → **CAPO / OPRO via offline `promptolution`**, triggered **only when
  GEPA aborts on budget**. **Same setup, swap optimiser only**: same T2-only scalar,
  same λ floor + penalty, same K≥3, same gate-check unlock. `promptolution` is
  **offline-native**, so the fallback is the fully-offline-loop variant (no cloud
  reflector). New dependency (`promptolution`; or `gepa`/`dspy` for GEPA) is an
  **online install at setup time only** — out of the offline-at-serve constraint.

- [x] **19.1 Deep-research survey** — **DONE** (2026-06-22, run `wf_a1a936f3-24f`).
      Findings + citations: `docs/structured-optimisation-research.md`.
      **Verdict:** **GEPA** (reflective Genetic-Pareto prompt evolution) is best-fit
      — optimises the real harness levers (prompts, tool descriptions via MCP
      adapter, multi-module tool selection), reflects on each rollout for max signal
      (ideal when evals are slow), and is the *only* technique with a documented
      **fixed-model coding-agent win** (Mini-SWE-Agent **55%→82%** on Jinja by
      evolving skill docs — the gskill / `optimize_anything` pipeline, ~300
      SWE-smith tasks/repo). Fallback: **CAPO / OPRO** via the offline
      **promptolution** package (only family with tiny-budget evidence on the open
      Gemma family).
      ⚠ **Gaps:** no study used a Gemma-4-E4B-class optimisee at offline tok/s;
      "few rollouts" is RL-relative (total is hundreds–thousands); gains can regress
      (GEPA lost on SST-5). **And item 16's local evidence says prompt changes alone
      didn't help here** — so treat GEPA as strong-on-fit, unproven-on-our-stack.
      **[lit-only]** per the Evidence policy: the GEPA verdict is citation-checked,
      not measured here. 19.3 is its local validation — and it must also test the
      *counter-arm* (does prompt/skill optimisation move the local pass-rate at all,
      or does item 16's "prompt changes don't help here" finding hold under a
      controlled run, not just hand trace-review?).
- [x] **19.2 Feasibility filter (gate + budget).** ✓ **DONE 2026-06-26 — VERDICT:
      UNLOCKED.** All four ticks below pass; 19.3 may run (small-N, abort→fallback).
      Code shipped in `scripts/harness_eval.py` (`gepa_fitness` / `gepa_gate_check` /
      `gepa_krun_stats` / `gepa_tier_cell` / `gepa_rollout_wall` / `gepa_budget` /
      `gepa_assert_serving_offline`, `gepa-gate` subcommand + `make gepa-gate`);
      `make check` green, selftest 63/63. Full write-up:
      `docs/structured-optimisation-research.md` §19.2.
  - [x] **(gate) T2 climbable-gradient check.** ✓ Fresh **K=5** baseline re-measure
        (`gepa-gate-r1..r5`): T2 fracs `[.667, .667, .833, .833, .667]` → **mean 0.733,
        spread 0.167, headroom 0.267**. Unlock rule `0<0.733<1.0 AND 0.267>0.167` →
        **PASS / UNLOCKED**. *(A stale K=3 read with a lucky 6/6 outlier would have
        gated at spread 0.333; K=5 shows true noise is ~1 instance.)* (precondition (2) MET.)
  - [x] **(timing) Per-T2-rollout wall-clock micro-task.** ✓ **median 78.5 s/rollout**
        (n=30); per-candidate `78.5×6×K` = **23.6 min (K=3) / 39.2 min (K=5)**; full
        micro-run compute ≈ 12.5 min. **Budget: N≈10 candidates needs ~4 h awake compute
        at K=3** → 19.3 runs small-N with abort→CAPO/OPRO fallback. (`gepa_budget`.)
  - [x] **(reflector) Confirm the reflector wiring is loop-only.** ✓ `gepa_assert_serving_offline`
        guards the **evaluated** config: text levers only (`system_prompt`→AGENTS.md via
        `apply_levers`, tool/skill text via `opencode_config`, `sampling`, `env`); rejects
        any `external_provider`/`model_ref`/`base_url` flip or non-`mlx-local` provider.
        Reflector may be cloud (consumes captured `opencode.jsonl` traces) but never sits
        in `cmd_run`'s serve path. Selftested.
  - [x] **(fitness) Confirm `tier-report.jsonl` is cheap enough as the inner-loop fitness
        read.** ✓ `gepa_tier_cell`/`gepa_krun_stats` are pure ledger aggregation (no model,
        no re-run); `score = T2_frac − λ·penalty` (λ=100) + T1 hard gate compute correctly
        from it (demonstrated on the live K=5 data + 11 selftests).
- [x] **19.3 Prototype GEPA** — ✓ **DONE 2026-06-26 — VERDICT: ADOPT (modest local win).**
      Reflector=Opus 4.8 (in-loop, item-18 pattern); optimisee+evaluator=frozen local
      Gemma; serving offline throughout. Converged in **2 candidates**, well inside budget.
      **Result: cand2 (terse positive-only rules, 233 ch) lifts T2 0.733→0.917 (K=6,
      Δ+0.183 > spread 0.167; floor 1.6→0.5; T1 held).** cand1 (verbose +numeric example,
      1025 ch) REGRESSED to 0.278 → **prompt LENGTH is the dominant lever on this weak 4B
      model: terseness helps, elaboration hurts.** Refines item-16's "prompt changes don't
      move this harness" (they do — in the less-is-more direction). Full write-up:
      `docs/structured-optimisation-research.md` §19.3. Adopted config:
      `scripts/harness_micro_configs/gepa-cand2.json`.
  - [x] Fitness = **`T2_frac − λ·(rise in no-edit+error+catastrophic-edit)`** with **λ
        large** + **T1 hard gate**; T3/T4 weight 0. ✓ shipped in 19.2 (`gepa_fitness`,
        λ=100, selftested) — reused as-is by 19.3.
  - [x] **Reflector loop (serving offline), T2 budget.** ✓ Opus-4.8 in-loop reflector
        (diagnose traces → propose `rules.content` edit); `gepa_assert_serving_offline`
        guards every candidate; eval is local-Gemma-only. N=2 candidates × K=3 (+ K=3
        re-val), inside the 19.2 ceiling → **no CAPO/OPRO fallback needed.**
  - [x] **Counter-arm:** ✓ **cand1** is the fixed-candidate-vs-baseline arm — a naive
        reflective prompt edit (more guidance) **regressed** T2 0.733→0.278, consistently
        (1/6,2/6,2/6, well beyond spread). Validates item-16's negative claim under a
        controlled run, *then refines it*: the wrong-direction edit hurts; the
        right-direction (terser) edit (cand2) helps.
  - [x] **Offline re-validation before adopt:** ✓ cand2 re-run independently K=3
        (reflector never in eval path): online K=3=1.0, **re-val K=3=0.833**, combined
        **K=6=0.917**. Win survives (re-val within one spread of online, stays above
        baseline, floor held) → **adopt**. (Honest effect ≈ T2 0.92, not a clean 1.0.)
  - [x] **Fallback:** not triggered (converged inside budget; `promptolution` unused).
  - [x] **Valid outcome (closed, per Evidence policy):** **adopt a candidate** (cand2);
        the [lit-only] GEPA verdict is now replaced by a measured local result.
  - [x] **`make check` (ruff + mypy + pytest) green** ✓ + selftest **66/66** (covers the
        fitness scalar + λ penalty + T1-gate + gate unlock + compare/reflection logic).

### Documentation (item 19)

- [x] **Update** `docs/structured-optimisation-research.md` — **DONE.** §19.2 (fitness
      scalar, λ=100 floor, T1 hard gate, serving-offline guard, K=5 gate verdict UNLOCKED,
      timing/budget) **and** §19.3 (the measured GEPA run: cand1 regressed, cand2 ADOPTED
      T2 0.733→0.917, counter-arm + offline re-validation) — replaces the **[lit-only]**
      GEPA verdict with a local measurement.
- [x] **Update** `docs/tiered-harness.md` — **DONE.** Documented `tier-report.jsonl` as the
      GEPA fitness read + the `score = T2_frac − λ·penalty` + T1-hard-gate definition.
- [x] **Update** `docs/opencode-local.md` (master doc) — **DONE.** Recorded item 19's
      ADOPT outcome (cand2 terse rules) as a lever result.
- [x] **Update** `CHANGELOG.md` — **DONE.** Item 19 closed entry (gate UNLOCKED + GEPA
      ADOPT cand2), mirroring the item-17/21 pattern.

### 20. Planning-first phase / orchestration topology  ← deep-research item

**Goal.** Decide whether to add a **dedicated planning phase before execution**,
and how much orchestration machinery is worth it for a weak local model. **Item-16
gate now SATISFIED** (closed 2026-06-25, `CHANGELOG.md`) — the E0 instrumentation 20.3
needs exists, and item-16 established the 0/8 is capability-bound (no degenerate-loop
fix landed; the loop modes were not the bottleneck). Opencode mechanics confirmed: it
natively ships a read-only
**`Plan`** primary agent + a **`Build`** primary agent, and a **`task`** tool that
delegates to subagents (`subagent_type`, background, resume).

- [x] **20.1 Deep-research survey** — **DONE & VERIFIED** (run `wf_48ab6f58-da0`;
      verification + synthesis completed 2026-06-23). Full report (18 sources, 18
      confirmed / 7 refuted): `docs/orchestration-planning-research.md`.
      **Verdict:** a full orchestrator-only main loop with a sub-agent chain is most
      likely a **net loss at 8–12 tok/s**; a **constrained plan-then-build
      separation** is the part worth prototyping. Three design-changing results:
      1. **Plan TYPE must match capacity** — weak models do *worse than no plan* with
         detailed how-to plans; **goal-style** (what-to-achieve) plans help
         (Llama-1B: None 25.2% → Guideline 23.2% → Goal 30.2%).
      2. **Unrestricted "full thinking" induces our exact pathology** — 4B collapsed
         16.28%→3.49% via "tool-call loops ending in `<tool_call>` / non-termination";
         planner-only thinking helps, tool-use > thinking. Keep the executor thin.
      3. **The benefit is within-policy lookahead, NOT a sub-agent** — a single
         lookahead step provably dominates flat greedy; so you likely don't need a
         second *agent* at all.
      **Against heavyweight orchestration (verified):** 1–2 orders of magnitude more
      tokens (15×/10–100×/4–220×); does not consistently beat a single agent on
      coding (single general agent 16/19; one model dropped 13/19→8/19); orchestrator
      = single point of error propagation. Anthropic's "90.2% win" was **refuted**.
      **Tension resolved:** keeping the executor thin (minimal tools, minimal
      thinking) *aligns* with item-11's "drop `task`/shrink decision surface" — you
      do NOT need the subagent tool to get the planning benefit.
      The 20.1 verdict is **[lit-only]** — citation-checked against papers, **not**
      measured on this stack; none of the sources tested a Gemma-4-E4B coding harness.
      Per the Evidence policy it is a *hypothesis* until 20.3 validates it here.
- [ ] **20.2 Decision: minimal viable shape.** Bet = one **bounded goal-style
      planning pass → a thin flat ReAct executor**. Candidate implementations,
      **cheapest first** (compare on loop-rate + wall-clock):
      (a) **single-pass constrained template** — emit a short *goal* plan then the
      first tool call in ONE rollout (no second agent; zero extra rollouts —
      *likely best cost/benefit*); (b) opencode native **`Plan` primary → `Build`
      primary** (no `task` tool); (c) a true separate planning sub-agent (most
      expensive — only if a/b underperform). **Plan content = goal, not how-to.**
- [ ] **20.3 Local-harness validation — multi-arm A/B (the actual evidence).** Run
      ALL arms on this machine on item-16's E0 instrumentation; adopt/reject from the
      **local numbers**, not the literature. Arms:
      1. **baseline** — current flat ReAct loop;
      2. **planning-first** — the 20.2 winner (goal-style plan → thin executor);
      3. **minimal multi-agent** — orchestrator + plan sub-agent + build sub-agent
         (opencode `task` tool). ← **the counter-arm: validates the NEGATIVE claim**
         ("multi-agent is a net loss here") instead of assuming it from papers.
      Metrics per arm: **degenerate-loop rate** (primary), full-harness pass-rate,
      and **tokens + wall-clock per task** (does multi-agent really cost 8–15× *here*?).
      **Decisions this run must settle locally:** does planning-first *lower or raise*
      the loop rate (no source answers this)? is multi-agent actually worse on *this*
      stack, or did the literature mislead? Item-16 gate satisfied (E0 metrics exist).

### 23. GEPA on the next rung — T3 (single-file real fixes) via a shaped reward + longer run  ▲ — follows item 19

**Goal.** Item 19 closed with **ADOPT on T2** (terse rules, T2 0.733→0.917) — the first
prompt lever that moved a tier on this stack. Push GEPA up to the **next rung, T3**
(single-file, single-hunk, single-F2P **real** SWE-bench fixes), with a **longer run**
(bigger N) and an **Opus-4.8 in-loop reflector** (same wiring as 19.3). T3 is the lowest
real-code rung — if any prompt/skill lever can crack it, this is where it shows.

> **⚖ Tension with item 19's design (acknowledged, deliberately tested).** Item 19's
> design said *"do NOT attempt a T3/T4 GEPA run until the capability wall moves"* and
> weighted T3/T4 at 0 because **binary** T3 is a flat **0/3** — no gradient for GEPA to
> climb (the same reason 19.2 used T2). This item does **not** assume the wall moved; it
> tests whether a **shaped (dense) reward** exposes a *climbable sub-signal* underneath
> the flat binary, and whether a longer run can convert it. **A clean "no movement even on
> the shaped signal" is a valid, wall-confirming outcome** (Evidence policy) — not a
> failure of the item.

### Failure investigation (measured on the on-disk baseline corpus, 2026-06-26)

The 3 T3 instances are the **easiest real fixes** — each is 1 file · 1 hunk · **1 F2P
test**, ~8.1K context, expected tool seq just `[read, edit]`, gold flips F2P with P2P
fully intact. Yet baseline is **0/3**, and crucially **they fail in three DISTINCT
modes** (per-instance over the baseline K-run repeats):

| T3 instance | F2P | dominant failure | signature | what's missing |
|---|---|---|---|---|
| **21614** | `test_Derivative_kind` (6 P2P) | **near-miss / wrong-fix** | **edits cleanly, P2P 6/6 intact, F2P still 0/1**; also times out | the *fix content* is wrong — everything else is right |
| **12481** | `test_args` (7 P2P) | **no-tool-stop** | 142 tok, **0 tool rounds**, dropped output, never engages (but `nothink` once got it to 15-step edit) | doesn't even start — emits prose and stops |
| **21627** | `test_Abs` (26 P2P) | **tool-churn** | **8 search/read rounds, never edits**, then no-edit/timeout | explores but won't **commit** to an edit |

**Read:** T3 is not one wall but three different failure modes — *engage* (12481),
*commit-to-edit* (21627), and *get-the-fix-right* (21614). 21614 is a genuine **near-miss**
(it edits without regressing P2P; only the F2P content is wrong), which is the strongest
evidence there is *some* headroom. The first two are **behavioural** (engagement/
termination — exactly the family GEPA moved on T2); the third is **reasoning** (hardest).
*(These 3 are the historical baseline; 23.1 expands the T3 tier to ~6 and re-baselines for a
finer shaped gradient — see Design decisions.)*

### The core enabler — a SHAPED T3 reward (precondition, mirrors the 19.2 gate-check)

Binary T3 = 0/3 gives GEPA nothing to climb. **23.1 must first build a dense per-instance
reward** that scores the progression the modes above expose, so the optimiser sees a
gradient. It is a **TOTAL function** over every terminal (every `reason` × E0-metric
combination maps to exactly one rung — full table in 23.1), keyed off `made_edit`,
`pass_to_pass_*`, `fail_to_pass_*`, **`tool_call_rounds`**, and `reason`:

> `catastrophic-edit / hard-fail (oom|error) (−0.25) < no-tool-stop (0.0) <
> tool-churn / explored-no-edit (+0.25) < edited, P2P intact, F2P fail (+0.50) <
> F2P flips (+1.0)`

Rung predicates (resolved plan-review 2026-06-26):
> - **−0.25** — an edit that REGRESSED P2P (catastrophic), OR a hard-failure terminal
>   (`oom`/`error`): strictly *below* honest non-engagement, so "break working code / crash"
>   can never out-score "don't start". **This replaces item-19's separate λ penalty** — the
>   penalty is now baked into the per-instance score, not an aggregate term.
> - **0.0** — no-tool-stop: `made_edit=False AND tool_call_rounds == 0` (emits prose / drops
>   output and stops without acting).
> - **+0.25** — tool-churn / explored-no-edit: `made_edit=False AND tool_call_rounds >= 1`
>   (engaged tools but never committed an edit). `tool_call_rounds` is the discriminator that
>   separates this rung from no-tool-stop — both are `no-edit` in the item-17 taxonomy.
> - **+0.50** — `made_edit=True AND P2P intact AND F2P fail`. **Timeout does NOT cap this
>   rung** — a clean, P2P-intact edit that also hit the wall-clock cap (21614's signature)
>   still scores 0.50; the edit is what matters.
> - **+1.0** — F2P flips (real fix).

The **binary F2P-flip stays the ultimate adopt gate** — the shaped reward is *only* the GEPA
climbing signal, never the success criterion. (The old "λ floor" wording is retired: the floor
is now enforced two ways — the −0.25 catastrophic/hard-fail rung *in* the score, and the
**T1+T2 hard gates** in the fitness, below.)

### Design decisions (resolved — plan-review 2026-06-26)

- **Fitness** → `score = T3_shaped_mean` — the shaped mean is the **only** climbing term;
  **no aggregate λ penalty** (retired; the catastrophic/hard-fail penalty is the −0.25 rung
  baked into the per-instance score). **T1 AND T2 are BOTH HARD GATES**: a candidate that
  drops T1 *or* T2 below baseline is **rejected outright** (not soft-penalised) — a T3-
  targeted lever must never erode the adopted T2 0.917 win or the tool-call floor. This
  **reworks `gepa_fitness`** (item 19's `T2_frac − λ·floor_rise` → `T3_shaped_mean` + a
  second hard gate); `gepa_assert_serving_offline` is reused as-is. The cheap ledger read is
  reused; the shaped score is a NEW total per-instance function over `made_edit`,
  `pass_to_pass_*`, `fail_to_pass_*`, **`tool_call_rounds`**, `reason`.
- **Gate-check — TWO ceilings.** Re-measure the shaped T3 mean at K≥3 and apply the **19.2
  unlock rule** `(ceiling − mean) > K-run spread` with **ceiling = 0.50** — the *behavioural*
  ceiling ("every instance edits with P2P intact"), the most a *text lever* can realistically
  reach, since the F2P-flip is capability-bound. **Unlock the climb on 0.50**; keep **ceiling
  = 1.0 (binary F2P flip) as the SEPARATE adopt gate**. Report both. If the shaped signal is
  flat/noise-dominated under the 0.50 ceiling ⇒ **gated**, record "T3 wall holds even under
  shaping" (a closed negative). 21614's intermittent edit+P2P-intact and 12481's `nothink`
  engagement suggest the mean is non-zero with real variance → plausibly climbable.
- **T3 corpus — expand to ~6.** The 3 on-disk T3 instances give a mean over only 3 discrete
  rungs → too coarse a gradient. **Mine ~3 MORE single-file/single-hunk/single-F2P real fixes
  OFFLINE** from the already-downloaded SWE-bench corpus (same selection criteria) into a
  **NEW 6-instance item-23 frozen baseline** (re-measure baseline K-runs on all 6). The old
  3-instance T3 numbers (items 17/19) stay **historical** — not comparable to the new 6-set.
  This is a 23.1 prerequisite; **if 3 qualifying offline instances can't be sourced +
  re-baselined in budget, fall back to the 3-set and note the coarse-signal caveat.**
- **Reflector / optimisee / serving** → identical to 19.3 (Opus 4.8 in-loop reflector; frozen
  local Gemma optimisee+evaluator; serving offline; text levers only;
  `gepa_assert_serving_offline` guards every candidate).
- **Budget — TWO-PHASE go/no-go.** T3 rollouts are ~**8–12× more expensive than T2** (a real
  fix runs to the ~600 s cap vs T2's ~78 s). **Phase 1 = a cheap N≈3 probe** gated by the
  23.1 shaped gate-check; **only unlock the longer N≈8–12 run (Phase 2) if the probe clears
  spread on the 0.50 ceiling.** Per candidate ≈ `~600 s × 6 instances × K=3` ≈ **3 h** → a
  full Phase-2 run is many hours, so it is **chunked across sessions, resuming at candidate
  boundaries** (each candidate's K rollouts complete in one session; a mid-candidate suspend
  discards that candidate's partial rollouts; persisted state = the JSONL ledger + saved
  candidate configs + a small frontier/tried file the reflector reloads). Size precisely in
  23.1 from a measured T3 rollout median (reuse `gepa_budget`); keep the abort-ceiling +
  CAPO/OPRO fallback.

### Scenarios to try (the candidate hypotheses — each a text-lever-only edit, mode-matched)

- **(a) Engagement / anti-no-tool-stop** *(targets 12481)* — a rule forbidding a
  prose-only turn: "never end a turn with only text until the fix is saved; every turn
  emits a tool call." Pair with **`nothink`** (already shown to flip 12481 from
  no-tool-stop → 15-step edit). Tests whether the no-tool-stop is a thin termination lever.
- **(b) Commit-to-edit / cap exploration** *(targets 21627)* — "once you've located the
  buggy lines, make the `edit` immediately; do not keep searching. Cap yourself at ~3
  search/read rounds before editing." Tests whether tool-churn is a budgeting lever.
- **(c) Verify-against-the-failing-test** *(targets 21614, the near-miss)* — "before
  finishing, restate what the failing test asserts and confirm your edit produces that."
  The hardest (reasoning), but 21614 is already P2P-clean — only the fix content is wrong.
- **(d) Transfer the T2 winner** — seed with item 19's adopted **`gepa-cand2`** terse rules:
  does "less is more" transfer from T2 tool-fidelity to T3 real fixes, or is T3 a different
  regime? **Note (resolved): cand2's text is a `rules.content` lever that ONLY the micro suite
  reads; the full harness has no `rules` channel — it applies `system_prompt` → `AGENTS.md`.**
  Port cand2's terse text into a new `harness_configs/*.json` so it **APPENDS** to the opencode
  default (replicate the micro `rules` append, so the comparison is apples-to-apples with item
  19 — NOT a `system_prompt` REPLACE; if the `system_prompt`→`AGENTS.md` path replaces rather
  than appends, 23.2 must add an append channel). A clean transfer datapoint, but the lever-
  channel port makes it a fresh measurement, not a guaranteed carry-over.
- **GEPA then evolves these** via the shaped reward — the seeds are reflector starting
  points, not the final answer; the longer N lets it combine/mutate them.

### Sub-tasks

- [ ] **23.1 Corpus expand + shaped T3 reward + budget sizing + T3 gate-check** (the precondition).
  - [ ] **Expand the T3 tier to ~6.** Mine ~3 more single-file/single-hunk/single-F2P real
        fixes OFFLINE from the already-downloaded SWE-bench corpus (same criteria as the
        original 3); add to `harness_eval_subset.json`. **Re-measure a NEW 6-instance frozen
        baseline** (K-runs); old 3-instance numbers kept historical. If 3 qualifying offline
        instances can't be sourced/re-baselined in budget ⇒ fall back to the 3-set + note the
        coarse-signal caveat.
  - [ ] **Add the TOTAL shaped per-instance score** over `made_edit`/`pass_to_pass_*`/
        `fail_to_pass_*`/**`tool_call_rounds`**/`reason` (rungs −0.25 / 0.0 / +0.25 / +0.50 /
        +1.0 per the table above; timeout does NOT cap a clean P2P-intact edit; `oom`/`error`
        = −0.25). **`make check`-green selftest covering EVERY terminal → rung** (the totality
        check, incl. 21614's timeout-with-clean-edit = 0.50 and oom/error = −0.25).
  - [ ] **Rework `gepa_fitness`**: `score = T3_shaped_mean` (no λ term) with **T1 AND T2 both
        hard gates** (reject if either drops below baseline). Selftest the two-gate logic.
  - [ ] **Budget + gate-check.** Measure a T3 rollout median (K=3) → size Phase-2 N + abort
        ceiling via `gepa_budget` (6 instances). Run the **two-ceiling** gate-check: unlock the
        climb on **ceiling 0.50**, report **1.0** as the adopt gate. **Gate fails (flat/noise
        under 0.50) ⇒ stop, record "T3 wall holds under shaping".**
- [ ] **23.2 Seed the mode-targeted candidates** (a)–(d) as `harness_configs/*.json` (text
      levers only; `gepa_assert_serving_offline` guards each). **(d) ports cand2's terse text
      as an APPEND** to the opencode default (match the micro `rules` append; if the
      `system_prompt`→`AGENTS.md` path replaces rather than appends, add an append channel here).
- [ ] **23.3 The GEPA run — TWO-PHASE go/no-go.**
  - [ ] **Phase 1 — cheap N≈3 probe** over the shaped T3 fitness (Opus-4.8 in-loop reflector).
        **Only unlock Phase 2 if the probe clears spread on the 0.50 ceiling**; else close
        (negative, wall holds).
  - [ ] **Phase 2 — the longer run** N≈8–12 × K=3, **chunked across sessions, resume at
        candidate boundaries** (each candidate's K rollouts finish in one session; persisted
        state = JSONL ledger + saved candidate configs + a frontier/tried file; mid-candidate
        suspend discards that candidate's partials). Abort→CAPO/OPRO fallback. Track best by
        the shaped score **and** any binary F2P flip.
- [ ] **23.4 Counter-arm + verdict.** Counter-arm = a fixed candidate vs baseline K≥3 on the
      shaped signal. **Valid outcomes (all closed):** (i) **a real F2P flip** on ≥1 T3 instance
      (breaks the 0/8 wall — major; the 1.0 adopt gate); (ii) **shaped-signal moves but no
      binary flip** (partial — a shaped-mean gain clearing the K-run spread with T1+T2 hard
      gates + P2P held; records which mode is prompt-movable, e.g. engagement yes / fix-content
      no); (iii) **no movement even on the shaped signal** (the capability wall holds at T3,
      now validated under shaping, not assumed). Offline re-validate any adopt.
- [ ] **`make check` green** + selftests for the shaped reward (totality), the two-gate
      fitness, and the two-ceiling gate logic.

### Measurement plan (item 23)

- **Climbing signal:** shaped T3 mean (K≥3) with the 19.2 unlock rule at **ceiling 0.50** (the
  prompt-reachable behavioural ceiling). **Adopt gate (separate, ceiling 1.0):** a binary F2P
  flip — or, for a partial outcome, a shaped-mean gain that clears the K-run spread with **T1
  AND T2 hard gates held** + P2P intact. **Primary caveat:** never let a P2P-regressing edit
  (or an `oom`/`error` crash) count as progress — both sit at the **−0.25 rung**, strictly
  below honest non-engagement.
- **Per-arm metrics:** shaped score, binary F2P pass/6, made-edit rate, P2P-intact rate,
  `tool_call_rounds` (the no-tool-stop vs tool-churn discriminator), the per-mode breakdown
  (no-tool-stop / tool-churn / near-miss), wall-clock per rollout.
- **Frozen baseline = the NEW 6-instance T3 set** (re-measured in 23.1), kept throughout the
  run; the old 3-instance T3 numbers are historical. **T4 explicitly out of scope** (multi-
  file, harder).

### Documentation (item 23)

- [ ] **Update** `docs/structured-optimisation-research.md` — append §23 (shaped T3 reward,
      gate-check, the longer-run result; whether the wall moved).
- [ ] **Update** `docs/tiered-harness.md` — note the shaped T3 reward as the GEPA climbing
      signal for the real-code rung (vs the binary tier pass used for adopt).
- [ ] **Update** `docs/opencode-local.md` + `CHANGELOG.md` only when item 23 closes.

### 21. Sandboxed code-execution ("code mode") — DONE (see `CHANGELOG.md`)

**Item 21 is fully complete and recorded in `CHANGELOG.md`** (21.1 survey → 21.2a/b
local code-gen gate PASSED → 21.3 round-trip A/B → 21.4a wired into real opencode →
21.4b production A/B → 21.4c niche firm-up). **Net verdict:** code-mode is viable on
this stack and **kept enabled/available** — it is a confirmed **reliability + latency**
win (never times out, ~1.9–2.9× faster, fewer round-trips) on genuinely multi-step /
bash-hostile tasks, and the model selects it ~75% of the time there. **But it is NOT a
correctness win on the frozen weak model** (21.4c: overall correctness regressed
0.80→0.55 — it single-shots a buggy answer where the baseline's grep-churn grinds to a
correct one), so **no default-on global nudge** is added; the bottleneck shifts from
round-trip churn to code quality = the same item-16 capability wall. Revisit default-on
only if model capability moves (items 16/19).

- [x] **21.4c — firm up (k=5) + find code-mode's real niche.** DONE 2026-06-25.
      `scripts/codemode_niche_ab.py` (own ledger, offline selftest, ruff+mypy clean):
      4 bash-hostile tasks × k=5 × 2 arms vs the same bash-equipped baseline. Niche
      **confirmed as reliability/latency, not correctness** (termination 1.0 vs 0.8,
      wall 150.6s vs 286.9s, calls 1.55 vs 2.65; correctness 0.55 vs 0.80). Surfaced two
      facts: the sandbox is **builtins-only** (needs a `no-import` nudge) and the
      bash-equipped baseline **never used `bash`** on parse tasks (defaults to grep/read
      churn → timeouts), refining 21.4b. **Final: ADOPT (enabled), no forced default.**
      Full record + per-task numbers in `CHANGELOG.md`.

### 22. Online-model harness-soundness control (BigPickle / free opencode mode)  ▲ — diagnostic for item 16

**Goal.** Run the **exact same full-harness** (`harness_eval.py run`, identical SWE
subset + tools + scaffolding) against a **strong online model** — **BigPickle, the
free model available in opencode** — to **isolate harness mechanical bugs from
local-model capability**. Item 16's baseline is **0/8 with the frozen Gemma-4-E4B**;
that number is only interpretable as "capability-bound" once we've proven the
*harness* itself isn't silently broken. This is the missing control arm.

**Why it's decisive.**
- If BigPickle ALSO scores ~0/8 on the same subset → the **harness is broken** (a
  mechanical bug in tool wiring / patch application / scoring), and every item-16
  lever is chasing the wrong cause. **Fix the harness before trusting any 16 signal.**
- If BigPickle passes most/all → the harness scaffolding is **mechanically sound**;
  the local 0/8 is genuinely **model-capability-bound** (consistent with the
  no-tool-stop + tool-churn taxonomy, NOT degenerate loops), and item-16's framing
  holds. Bonus: BigPickle's failure-mode histogram becomes the "what a working run
  looks like" reference for item 16/17's 7-mode taxonomy.

**Constraint compatibility (non-negotiable — mirrors item 18's Opus-4.8 framing).**
This is a **diagnostic / CI control run only — NOT a serve-path change.** The frozen
local stack (Gemma-4-E4B / mlx-lm 0.31.3, fully-local-at-serve) is unchanged; the
online model is used **solely to validate the harness scaffolding** and is never
shipped or used at serve time. The run needs network and is therefore the one
explicitly **online** exception — run on demand, never in the offline serve path.

### Design decisions (resolved)

- **Gate scope = ALL THREE local-only assumptions, not just the provider block.**
  Code reading of `scripts/harness_eval.py` confirmed the local stack is wired in at
  three coupled points, all of which `external_provider` must short-circuit:
  (1) `apply_levers` (≈L348-389) writes the `mlx-local` provider block with
  `options.baseURL` → `base_url` and sets `model`/`small_model` → `model_ref`;
  (2) `cmd_run` (≈L1595) calls `server_healthy(args.base_url)` and **restarts MLX or
  aborts (`return 2`)** if the local endpoint is down; (3) `detect_model(args.base_url)`
  (≈L1604) queries the live MLX `/v1/models` to derive `served`, and `_score_subset`/
  `score_instance` carry OOM-restart logic (≈L1663). With the gate ON the run must work
  with **MLX fully off**: skip the provider block, skip the health-check/restart, skip
  `detect_model`/OOM-restart, and take `model_ref` straight from the config /`--model`.
- **Auth/connectivity pre-flight replaces the removed MLX health-check.** When
  `external_provider` is on, do one cheap pre-flight (a trivial `opencode run -m
  opencode/big-pickle` ping or auth-status check) before the subset loop; on failure
  abort with `run 'opencode auth login' + check network` instead of letting all 8
  instances fail opaquely.
- **Verdict is banded, with an explicit middle action.** On the 8-instance K≥3 subset:
  **≤1/8 ⇒ harness broken** (same dead-zone as Gemma); **≥5/8 ⇒ harness sound**;
  **2–4/8 ⇒ inconclusive**, which opens a harness-inspection sub-item (22.5).
- **Histogram is the PRIMARY evidence; pass-rate secondary.** The `failure_category`
  taxonomy (`FAILURE_CATEGORIES`, ≈L456-506) is **provider-agnostic** — derived from
  terminal `reason` + E0 metrics, never from model identity — so BigPickle drops into
  the same 10-category vocabulary with zero code change. The "harness sound" signature
  is: BigPickle landing mostly in **`ok`/`tests-failed`** (capability modes) with **ZERO
  `oom`/`degenerate-loop`/`no-edit`/`edit-mismatch`** (mechanical/harness modes).
- **"Identical" = held constant where it proves soundness, provider-appropriate
  elsewhere.** Hold **tools + prompt + subset + scoring** byte-identical to the Gemma
  arm; ALLOW provider-appropriate sampling/context limits and a **shorter per-instance
  timeout** (the default 600s, ≈L103, is tuned for ~8-12 tok/s Gemma and would over-
  generously cap a fast gateway model). Every such delta is **recorded in the ledger
  `notes`** so the control is auditable.

- [x] **22.1 Gate ALL THREE local-only assumptions (harness code change — prerequisite).** ✓ DONE
      `external_provider` gate wired in `apply_levers` (omits the `mlx-local`/`baseURL`
      block, attaches sampling/limit under opencode's built-in provider), `cmd_run`
      (skips health-check/restart/detect_model; reads `model_ref` from config/`--model`),
      `score_instance` + `_score_subset` (skip the local OOM probe/restart). Startup line
      printed; `--external-provider` CLI flag added. selftest test #16 asserts no
      `mlx-local`/`baseURL` leak + pinned refs + forwarded sampling. `make check` green
      (ruff + mypy + selftest 41/41).
      Add a config flag `"external_provider": true` (and a matching `--external-provider`
      / inferred-from-config path) that, when set: (a) in `apply_levers`, **omits the
      `mlx-local` provider/`baseURL` block** so opencode's built-in `opencode` provider
      resolves the ref, and writes `model`/`small_model` from the config ref only; (b) in
      `cmd_run`, **skips `server_healthy`/restart**; (c) **skips `detect_model`** and takes
      `model_ref` straight from config/`--model`, and skips the local OOM-restart path in
      `_score_subset`. Print a startup line noting the online arm ("skipping MLX health-
      check / detect_model; requires network + opencode auth"). **Gate the change with a
      `selftest` assertion**: with `external_provider` on, the written `opencode.json`
      contains **no `mlx-local`/`DEFAULT_PROVIDER` block and no local `baseURL`**, and
      `model`/`small_model` equal the configured external ref. Run `make check`
      (ruff + mypy + pytest) on the touched file.
- [x] **22.2 Auth/connectivity pre-flight + wire the online provider + lever config.** ✓ DONE
      `online_preflight(model_ref)` added (checks `opencode` on PATH + a trivial
      `opencode run` ping; aborts pre-loop with an `opencode auth login` + network
      remediation). `scripts/harness_configs/online-bigpickle.json` created
      (`external_provider`+`model_ref: opencode/big-pickle`+`temperature: 0.0`+`timeout: 240`;
      its `description` is the in-ledger delta record). `cmd_run` resolves a config-level
      `timeout` (CLI `--timeout` still wins). `make harness-eval-online` target added (no
      `mlx-up` dep). Verified: config produces a clean opencode.json (no local leak) and
      the live pre-flight against `opencode/big-pickle` PASSES (free zen gateway, reachable
      with 0 stored credentials).
      Model ref = **`opencode/big-pickle`** (provider `opencode`, model `big-pickle` —
      verified present in `opencode models`, opencode 1.17.9; free hosted model via the
      opencode zen gateway, needs a one-time `opencode auth login` to the `opencode`
      provider and **network** at run time). With 22.1's gate in place the override path
      just sets `model`/`small_model` to `opencode/big-pickle` (`--base-url` unused).
      Add a **pre-flight** (auth-status + a trivial `opencode run` ping) that runs once
      before the subset loop when `external_provider` is on and aborts early with a clear
      remediation message. Reuse the existing online pattern in `scripts/codegen_probe.py`
      (`opencode_complete`, ≈L658; `bigpickle` target, ≈L702 — `transport="opencode"`,
      `opencode run -m provider/model`, no project `opencode.json`, global auth). Add a
      `harness_configs/online-bigpickle.json` lever config (sets the model ref + the
      `external_provider` flag + provider-appropriate sampling/timeout) so the run is one
      command and gets a distinct `config_name` in the ledger.
- [x] **22.3 Run the control + read the histogram.** ✓ DONE — **4/8** (`ok`) at the
      tightened 240s cap (`label online-bigpickle-22.3`), recorded to the ledger.
      Histogram: `ok`×4, `timeout`×2, `catastrophic-edit`×1, `no-edit`×1. Aggregate in
      the inconclusive band → triggered 22.5; **zero** mechanical modes already visible.
      Run the SWE K≥3 subset under
      BigPickle via the new online target, **holding tools/prompt/subset/scoring identical**
      to the Gemma arm and recording the allowed deltas (sampling/context/timeout) in the
      ledger `notes`. Record pass-rate + the full failure histogram to the JSONL ledger
      alongside the local baseline. **Banded decision gate:** **≤1/8 ⇒ harness broken**
      (open the bug sub-item, block item-16 interpretation); **≥5/8 ⇒ harness sound**
      (validate item-16's capability-bound reading); **2–4/8 ⇒ inconclusive ⇒ 22.5**.
- [x] **22.4 Record the verdict** ✓ DONE — **HARNESS SOUND**, recorded in `CHANGELOG.md`
      (Done items 17/21/22) + `docs/opencode-local.md` with the three side-by-side
      histograms. Framed on the histogram signature: at the Gemma-identical 600s cap
      BigPickle's failures are 100% capability modes (`ok`/`tests-failed`/`catastrophic-
      edit`) with ZERO `oom`/`degenerate-loop`/`no-edit`/`edit-mismatch`; decisive
      contrast is Gemma's 0 `ok` across 3 repeats vs BigPickle's 4 on identical
      scaffolding → local 0/8 is capability-bound, item-16 unblocked.
      Record the verdict in `CHANGELOG.md` / `docs/opencode-local.md` —
      harness sound vs broken, with the two failure histograms side by side. **Frame the
      verdict on the histogram signature, not just the aggregate:** call out that the
      "harness sound" evidence is BigPickle landing mostly in **`ok`/`tests-failed`**
      (capability modes) with **ZERO `oom`/`degenerate-loop`/`no-edit`/`edit-mismatch`**
      (mechanical modes) — pass-rate is secondary. One-shot control, not ongoing work;
      re-run only after structural harness changes.
- [x] **22.5 (conditional — triggered by 22.3's 4/8) — disambiguate bug vs. variance.** ✓ DONE
      Traces read: PASS sympy-15345 captured a real `_print_Max/_print_Min` fix → 10
      tests passed (pipeline sound); FAIL sympy-19007 was a genuine `length` output-budget
      cutoff (3 grep + 2 read, **zero** edit attempts) — not a harness mis-capture. Re-ran
      all 4 failures at the Gemma-identical 600s cap (`label online-bigpickle-22.5-retry600`):
      both 240s timeouts complete (57s / 382s) and ALL 4 resolve to `tests-failed`/
      `catastrophic-edit` (sympy-19007 → F2P **1/3** partial, proving the scorer reads real
      pytest results). Outcome: the inconclusive aggregate is driven by genuine
      model-capability failures + a timeout-cap artifact, NOT a mechanical harness bug
      → verdict resolves to **SOUND**.
      Manually read one **passing** + one **failing** BigPickle trace
      from the per-instance artifacts the harness already saves (`run_dir/opencode.jsonl`
      + `opencode.log`) and write a short defect note; **AND re-run the 2–4/8 instances at
      higher K** to distinguish a real mechanical harness bug from gateway-side
      run-to-run nondeterminism before declaring the verdict.

### Documentation

- **Update** `docs/opencode-local.md` (master doc) — record the `external_provider`
  gate + the online-control arm + the final harness-sound/broken verdict with the two
  side-by-side histograms.
- **Update** `CHANGELOG.md` — the 22.4 verdict entry (harness sound vs broken).
- **Update** `Makefile` — add `make harness-eval-online CONFIG=...` (no `mlx-up`
  dependency; documents the network requirement + the one-time `opencode auth login`),
  and fix the existing `harness-eval` comment (≈L71 "Needs the stack up (make mlx-up)")
  to note the online exception.
- **Update** `docs/opencode-config.md` if the `external_provider` flag changes the
  documented opencode-config builder behaviour (the provider-block omission path).
- **Add** `scripts/harness_configs/online-bigpickle.json` (its `description` is the
  in-ledger doc of the deltas held vs. varied).

---

## Notes / open questions

- **Sequencing.** 16 → (18, 19, 20). Item 16 is the prerequisite: a mechanically
  broken full harness can't give signal for 19's optimiser or 20's planning A/B.
  (Item 17's tiered harness is DONE — it supplies the gradient/fitness signal those
  downstream items consume.) **Item 22 is a cheap control that should run early:** it
  proves the full harness is mechanically sound (online BigPickle passes where the
  frozen Gemma fails) before item 16 spends effort on levers that assume the 0/8 is
  capability-bound rather than a harness bug.
- **Shared failure vocabulary.** Item 16's 7-mode taxonomy = item 17's (now-shipped)
  `failure_category` enum = item 18's trace-detection targets. Defined once.
- **Optimiser-cost tension.** Any search-based optimiser (GEPA/CAPO/OPRO) needs many
  candidate evals; each is a slow local harness run → item 17.5 must be a fast,
  cheap inner-loop fitness function.
- **Reliability floor.** No change may regress tool-call validity; every candidate
  passes the tool-call round-trip check before it scores.
