# TODO — opencode-optimisations

The repo's running work-ledger. **Items 24, 25, 26, 27 and 28 are the open work** (24 = the
model-swap survey, added 2026-06-27; 25 = GEPA-optimise the multi-agent planning prompt;
26 = evaluate codegraph-class codebase-exploration tools for planning — both added
2026-06-28, following item 20; 27 = extend GEPA to optimise ONLINE optimisee models
(BigPickle-as-example, configurable), added 2026-06-28; 28 = a formal-verifier stage in a
plan→verify→implement multi-agent loop, 900 s cap, added 2026-06-28). **Completed
items 1–23 now live in `CHANGELOG.md`** (items 18, 19 and 20's
full ticked detail is also kept inline below for reference). **Item 20 (planning-first /
orchestration topology) closed 2026-06-28: verdict (ii) PARTIAL — planning-first does not
transfer (arms a/b within spread of bare); the multi-agent counter-arm is the only arm to ever
land a real T3 fix (22714, 4/6 K=6) at ≈bare cost (refutes 8–15×), but the mean gain does NOT
survive re-validation and the `task` mechanism never fires (config side-effect) → not a robust
adopt; cand2 OOM-regresses on T3. The 22714 bottleneck is OOM/timeout variance, not capability.** Item 16 (the dominant harness
bottleneck) closed 2026-06-25: the L0–L6 mechanical-lever sweep is complete and the 0/8
is **capability-bound, not a harness defect**. **Item 19 (GEPA) closed 2026-06-26: ADOPT
cand2 (terse rules, T2 0.733→0.917) — prompt length is the dominant lever on this weak 4B
model.** **Item 18 (recommender) closed 2026-06-26: the two-layer pipeline is validated
(18.0 backtest 3/3 recall=precision=1.0), but its top emitted config REGRESSED in the
decisive 18.3 A/B (no-edit 5→18, made_edit 16→2, tool-calls 167→34) → verdict REJECT —
replacing the long tuned system prompt with a terse one suppresses tool use; refines item
19's "terse helps" to "adding less helps, gutting the prompt hurts".** **Item 23 closed
2026-06-27: GEPA on T3 (real fixes) via a SHAPED reward — the gate UNLOCKED (shaped mean
0.153 > spread) but the Phase-1 probe found NO candidate clears spread; the two measured
append seeds both REGRESSED → verdict (iii) the T3 wall holds UNDER SHAPING. Refines item
18/19 further: even *appending* terse mode-targeted rules (not just replacing) regresses the
weak 4B on real fixes. (d cand2-transfer arm unrun — machine OOM-reclaimed mid-probe 3×.)
Lasting deliverable: the shaped-reward + `gepa-t3-gate` machinery + the 6-instance T3 set.**

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

### 20. Planning-first phase / orchestration topology  ✅ CLOSED 2026-06-28 → `CHANGELOG.md`

> **CLOSED — verdict (ii) PARTIAL.** Planning-first does NOT transfer to this stack (arms a/b
> within spread of bare; finding #1 fails). The multi-agent counter-arm (c) is the ONLY arm
> that ever lands a real T3 fix (sympy-22714, the correct `point.py` `evaluate` guard, 4/6 over
> K=6 — all others 0 flips) at ≈bare token cost (refutes the 8–15× literature), so it is NOT a
> uniform net loss — BUT its mean gain does NOT survive an independent re-val (online K=3 0.278
> → re-val 0.153 = bare; combined K=6 0.215, Δ+0.062 ≪ spread 0.292) and the win is mechanism-
> incidental (the `task` tool never fires; gain is a config side-effect, likely subagent
> descriptions as goal scaffolding). **NOT a robust adopt.** cand2 OOM-regresses on T3 (measures
> item-23's unrun "d" arm). The 22714 bottleneck is OOM/timeout variance, not capability → next
> lever is the resource wall. Lasting deliverables: 5 topology arm configs + the multi-arm
> shaped-T3 A/B path. Full detail kept below (ticked) + `CHANGELOG.md` + `docs/item20-20.3-results.md`.

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

### Design decisions (resolved — plan-review 2026-06-27)

> Decisions relayed via the coordinator (user picked "most-rigorous across the board");
> recorded here as the working spec. The 20.1 verdict stays **[lit-only]** until 20.3
> measures it on this stack.

- **Primary (climbing) signal → REUSE item-23's shaped per-instance reward** (the
  `−0.25 / 0.0 / +0.25 / +0.50 / +1.0` rung function, `gepa_t3_shaped_score` in
  `harness_eval.py`), reported as the **mean across the subset**. *Rationale:* the
  originally-specified primary (degenerate-loop rate) is a **non-bottleneck** (item 16:
  the real failure modes are no-tool-stop + tool-churn, not loops), and full-harness
  pass/8 is the flat **0/8 capability wall** with no gradient. The shaped reward is the
  dense behavioural signal items 19/23 had to build for exactly this reason. Degenerate-
  loop rate is **demoted to a secondary metric** (still emitted by E0, still reported —
  it just stops being the adopt signal).
- **Subset → the item-23 6-instance T3 set** (`scripts/harness_eval_subset.json`, the
  single-file/single-hunk/single-F2P real fixes: 21614/12481/21627 + 22714/18621/15346).
  *Rationale:* the shaped reward is keyed on `pass_to_pass_*`/`fail_to_pass_*`, so it only
  scores **T3/T4 real-fix** instances cleanly — the T1/T2 micro rungs have no P2P/F2P to
  map. Running 20.3 on the **same shaped-reward regime as item 23** makes the primary
  signal well-defined for **every arm**. (T4 optional/out-of-scope for the first pass —
  multi-file, harder, more expensive.) **Interplay with item 23:** same frozen 6
  instances and the same ~257 s-median T3 rollouts → **the bare-baseline shaped numbers
  from 23.1 (mean 0.153, spread 0.083, K=3) are the reusable bare-baseline reference**;
  do not re-measure that arm from scratch. Sequence 20.3 to share the expensive T3 rollout
  budget with item 23 rather than double-paying it.
- **Adopt gate (separate, two-ceiling pattern — mirrors item 23) →** the shaped mean is
  the **climb** signal under the **0.50 behavioural ceiling** (every instance edits with
  P2P intact — the most a topology/text lever can reach); the **binary F2P flip /6 (and
  full pass/8 holding) is the SEPARATE adopt gate** at ceiling 1.0. Report both. An arm
  "moves the signal" iff its shaped K-run mean beats the **bare baseline mean by more than
  the K-run spread** (the **19.2/23.1 unlock rule** `(other − baseline) > spread`, reused
  here as the A/B significance test). **Adopt** an arm iff it clears spread **AND** holds
  full pass/8 **AND** keeps tool-calls valid (the hard floor).
- **Baseline → BOTH reference arms** run: (i) **bare opencode default** (absolute floor,
  = the 23.1 bare baseline) and (ii) **default + item-19 `gepa-cand2` via `rules_append`**
  (the current *shipping* config). *Rationale:* the two brackets attribute the delta —
  bare→cand2 isolates cand2's contribution, cand2→planning isolates planning's *marginal*
  effect on top of what we already ship. **All planning/topology arms sit on the cand2
  base** so the only varying lever is the orchestration shape, not the rules text.
- **Lever-injection channel → `rules_append` (APPEND), never `system_prompt` (REPLACE).**
  Confirmed in `apply_levers` (`harness_eval.py:446-458`): `system_prompt`/`agent.prompt`
  REPLACE opencode's tuned default; `rules_append` writes a local `AGENTS.md` that is
  APPENDED. Item 18 proved REPLACE **suppresses tool use** on the weak 4B. The goal-plan
  nudge and the cand2 rules both ride `rules_append`.
- **Thinking control (finding #2: planner-thinks / executor-thin) →** for arm (a)
  (single rollout, no separable executor) operationalise as a **bounded goal-plan nudge
  via `rules_append`** ("open with a 1–2 sentence GOAL of what to achieve, then
  immediately call a tool") **+ `nothink` sampling** (already shown in 23.1 to flip the
  12481 no-tool-stop). **Acknowledged tension (documented, not hidden):** a single-pass
  rollout has no *hard* planner/executor split — the "thin executor" is approximated by
  keeping the default toolset + suppressing free-form thinking after the plan sentence.
  Arms (b)/(c) DO have separable roles → thinking is concentrated in the plan
  agent/subagent there.
- **Budget / abort discipline (item-23 pattern) →** size from the **measured T3 rollout
  median (~257 s, 23.1)** via `gepa_budget`; single-rollout arms ≈ `257 s × 6 × K=3` ≈
  **77 min/arm**; the **multi-agent arm is 8–15× tokens** (research finding #6) → **a
  go/no-go gate precedes arm (c)**, with a wall-clock ceiling and **abort → fallback to
  arm (a)** (the cheapest viable shape). **K≥3** mean discipline throughout.
  `gepa_assert_serving_offline` guards **every** arm (text/topology levers only; serving
  stays on the frozen local Gemma).
- **Arm scope → run ALL THREE arms now** (the user override of the gate-then-expand
  default), INCLUDING the multi-agent counter-arm — but each structural arm carries a
  **build-time feasibility precondition** (see 20.2): if Gemma cannot emit valid
  tool-calls under that topology at all, the arm is recorded as a **wall-confirming null
  result**, never silently skipped.

- [x] **20.2 Build the arm configs (NO run).** ✓ **DONE 2026-06-27.** Five arm configs
      built under `scripts/harness_configs/`, all passing `gepa_assert_serving_offline`
      and all on the cand2 APPEND base (no bare). `make check` green + 6 new item-20
      selftest checks (config load / serving-offline / no-REPLACE / arm-c task-tool +
      subagents / apply_levers materialisation). Feasibility smoke run (stack up, no OOM).
      Deliverables:
      - **Arm configs as `scripts/harness_configs/*.json`** (text/topology levers only;
        `gepa_assert_serving_offline` passes on each):
        - ✓ `plan-baseline-bare` — bare opencode default (= 23.1's reusable bare baseline).
        - ✓ `plan-baseline-cand2` — default + cand2 terse rules via `rules_append` (the
          shipping reference; ported from `gepa-t3-d-cand2port.json`).
        - ✓ `plan-arm-a-goalnudge` — cand2 base + a **bounded goal-style plan nudge**
          (`rules_append`, goal-not-how-to per finding #1). **`nothink` is a PROXY-process
          lever** (`MLX_PROXY_NO_THINK=1`, set when bringing the stack up), NOT a config
          field — documented as a run-time requirement for the arm-a 20.3 run (mirrors
          `no-think.json`); the config carries the goal nudge only.
        - ✓ `plan-arm-b-planbuild` — **single-run approximation** (user-chosen 2026-06-27;
          opencode can't auto-chain two PRIMARY agents in one headless run without `task`).
          Injected as a **procedural plan-then-build via `rules_append` (APPEND)** — the
          `agent.build.prompt` REPLACE channel was **rejected** per the never-REPLACE rule
          (item 18 suppression). Contrast with arm a: arm a = goal-only nudge, arm b =
          procedural plan→read→edit (tests finding #1 goal-vs-how-to on this stack).
        - ✓ `plan-arm-c-multiagent` — cand2 base + an orchestration nudge (`rules_append`)
          + `opencode_config` that **re-enables the globally-disabled `task` tool**
          (`"task": false` in `~/.config/opencode/opencode.json` *and* on the native build
          agent) and defines `planner` (read-only) + `coder` subagents (the counter-arm;
          rides raw in `opencode_config` as the schema has no first-class `task` lever).
      - **Feasibility precondition (smoke, sympy-21614 = the near-miss that edits cleanly
        bare) — DONE.** Full log: `docs/item20-smoke-notes.md`.
        - **Arm b: 2/2 samples → 0 valid tool-calls** (S1 narrated an *invalid prose-markdown*
          search → no-tool-stop; S2 stuck at step 0 → timeout). The procedural append
          **suppresses tool emission** on an instance that edits cleanly bare → **flagged
          likely wall-confirming null**. NOT aborted: runs in 20.3 at K≥3.
        - **Arm c: 8/8 VALID structured tool-calls** (grep/read completed) **but the model
          NEVER invokes `task`** — it ignores delegation and degrades to flat grep/read
          **tool-churn** → 360s timeout, no edit. Tool-calls valid; the **multi-agent
          mechanism is inert** here. Runs in 20.3 as the counter-arm with the mechanistic
          reason recorded.
      - **Plan content rule:** goal/what-to-achieve, **never** detailed how-to. ✓ honored
        (arm a goal-only; arm b procedural is the deliberate finding-#1 contrast).
- [x] **20.3 Local-harness validation — multi-arm A/B (the actual evidence).** ✓ **DONE
      2026-06-28 — VERDICT (ii) PARTIAL: arm c (multi-agent) is the only arm that EVER
      lands a real T3 fix (22714), but the mean gain does NOT survive re-validation.**
      All 5 arms run K=3 on the 6-instance T3 set (600s cap) + an independent K=3
      confirmation re-val of arm c, scored by the item-23 shaped reward. Full write-up:
      `docs/item20-20.3-results.md`. Results (online K=3):

      | arm | shaped mean (K=3) | Δ vs bare | clears spread? | F2P flips | per-rollout | avg tok |
      |---|---|---|---|---|---|---|
      | bare (reused 23.1) | 0.153 | — | — | 0 | 257s | 1688 |
      | cand2 base | 0.000 | −0.153 | **regress** | 0 | 518s | 1660 |
      | arm a (goal+nothink) | 0.097 | −0.056 | no | 0 | 187s | 1056 |
      | arm b (plan-then-build) | 0.083 | −0.070 | no | 0 | 66s | 329 |
      | **arm c (multi-agent)** | **0.278** | **+0.125** | **YES** | **3** | 455s | 1542 |

      **Online K=3 looked like a win** (arm c 0.278, Δ+0.125 > spread, 22714 flips 3/3) —
      **but the independent confirmation re-val corrected it:** re-val arm c 0.153 (=bare),
      22714 flips only **1/3** (r1 pass; r2 OOM, r3 timeout). **Combined K=6: mean 0.215,
      Δ+0.062 vs bare ≪ spread 0.292 → does NOT clear the significance test.** 22714 flips
      **4/6** overall — the **correct** `evaluate`-guard `point.py` fix — so arm c is the
      **ONLY arm that ever lands a real T3 fix** (all others 0 flips), but the win is
      **high-variance/OOM-bound, single-instance, and mechanism-incidental** (the `task`
      tool NEVER fires — multi-agent delegation stays inert; the gain is a config
      side-effect, likely the planner/coder subagent DESCRIPTIONS as goal-scaffolding).
      Cost ≈ bare (1542 tok vs 1688) → **refutes finding #6's 8–15×**. **NOT a robust
      adopt; do not ship arm c as default.** Planning-first (a/b) within spread of bare →
      finding #1 does not transfer; cand2 OOM-regresses on T3 (measures item-23's unrun
      "d" arm). **The binding constraint on 22714 is OOM/timeout variance, not capability**
      (correct fix produced 4×) → next lever is the resource wall, not more shaping. Arms run:
      1. **baseline-bare** + **baseline-cand2** (the two reference brackets);
      2. **planning-first** = arm (a) goal-nudge + `nothink` (the cheap, likely-best shape);
      3. **native Plan→Build** = arm (b);
      4. **minimal multi-agent** = arm (c), orchestrator + plan/build subagents via `task`
         ← **the counter-arm: validates the NEGATIVE claim** ("multi-agent is a net loss
         here") instead of assuming it from papers. **Gated by the 20.2 feasibility
         precondition + a go/no-go wall-clock gate before its expensive rollouts; abort →
         fallback to arm (a).**
      **Decisions this run must settle locally:** does planning-first *raise the shaped
      mean* over the cand2 base by more than the K-run spread (finding #1/#4 on *this*
      stack)? does it *lower or raise* the (secondary) loop rate (no source answers this)?
      is multi-agent actually worse here — in shaped mean AND in tokens/wall-clock — or did
      the literature mislead (finding #6/#7/#8)? **Valid outcomes (all closed, per Evidence
      policy):** (i) an arm clears spread on the shaped signal with the adopt gate held →
      **adopt that shape**; (ii) shaped signal moves but no binary flip → **partial**,
      record which mode the topology is movable on; (iii) no arm moves the shaped signal →
      the planning hypothesis does **not** transfer to this stack (the [lit-only] verdict
      is now locally falsified, a valid closed negative).

### Measurement plan (item 20)

- **Climbing signal:** item-23 shaped T3 mean (K≥3) over the 6-instance T3 set, with the
  19.2 unlock rule at the **0.50 behavioural ceiling**; **adopt gate (separate, ceiling
  1.0):** a binary F2P flip /6 with full pass/8 held + tool-calls valid.
- **The single lever varied:** the **orchestration topology** (flat ReAct → single-pass
  goal-plan → Plan/Build → multi-agent). The rules text is held at the **cand2 base** across
  all topology arms; the bare arm is the absolute floor reference.
- **Per-arm metrics:** shaped mean, binary F2P /6, made-edit rate, P2P-intact rate,
  `tool_call_rounds` (no-tool-stop vs tool-churn discriminator), degenerate-loop rate
  (**secondary**), and **tokens + wall-clock per task** (does multi-agent really cost 8–15×
  *here?*). K≥3 mean + spread reported per arm.
- **Frozen baseline = the item-23 6-instance T3 set**; the bare-baseline shaped numbers from
  23.1 (mean 0.153, K=3) are the reusable reference. T4 out of scope for the first pass.
- **Gate:** `make check` (ruff + mypy + pytest) green + a selftest for any new arm-config
  loading / feasibility-smoke logic touched in `harness_eval.py`. `gepa_assert_serving_offline`
  asserted on every arm config.

### Documentation (item 20)

- [x] **Update** `docs/orchestration-planning-research.md` — **DONE.** Appended a "20.3 LOCAL
      VALIDATION" section converting the **[lit-only]** verdict to the measured result (which
      arm moved the shaped signal; multi-agent not a uniform loss here but doesn't survive re-val).
- [x] **Update** `docs/tiered-harness.md` — **DONE.** Noted item 20 reuses the shaped T3 reward
      as its A/B signal (shared regime with item 23) + the `rules_append` topology arms.
- [x] **Update** `docs/opencode-local.md` (master doc) — **DONE.** New *Planning-first /
      orchestration topology* section recording the (ii) partial outcome.
- [x] **Update** `CHANGELOG.md` — **DONE.** Item 20 closed entry under *Done (items …20…)*
      with the full arm table + the re-val correction + verdict (ii) partial.

### 23. GEPA on the next rung — T3 (single-file real fixes) via a shaped reward + longer run  ✅ CLOSED 2026-06-27 → `CHANGELOG.md`

> **CLOSED — verdict (iii) the T3 wall holds UNDER SHAPING (a clean, wall-confirming
> negative).** 23.1 built the machinery and **UNLOCKED** the gate (shaped mean 0.153,
> headroom 0.347 > spread 0.083 — a real dense gradient under the flat binary 0/6). But the
> 23.3 Phase-1 probe found **no candidate clears spread on the 0.50 ceiling**: the two
> fully-measured mode-targeted append seeds both **REGRESSED** (a 0.083, b 0.097 vs 0.153),
> c (K=1) worse, d unrun. Every measured arm disturbs the one reliable near-miss (18621
> 0.50→churn) rather than converting headroom → **Phase 2 not unlocked.** **Refines item 19:**
> not only does *replacing* the prompt hurt (item 18), *appending* terse mode-targeted rules
> also regresses the weak 4B on real fixes. The lasting deliverable is **23.1** (shaped reward
> + two-gate fitness + `gepa-t3-gate` + the 6-instance T3 set), reusable when capability moves.
> Full detail kept below (ticked) + in `CHANGELOG.md`.

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

- [x] **23.1 Corpus expand + shaped T3 reward + budget sizing + T3 gate-check** (the precondition).
      ✓ **DONE 2026-06-26 — VERDICT: UNLOCKED (Phase-1 probe may run).** Shaped T3 mean
      **0.153** (K=3, run-means [.208,.125,.125]), spread **0.083**, headroom-to-0.50
      **0.347 > spread** → climbable. Binary F2P flips **0/6** (the wall holds on the adopt
      ceiling, as expected). Per-T3-rollout median **256.9 s** → per-candidate (6×K=3)
      **77 min**, **~2 candidates** fit a 3 h wall. Rung tally over 18 instance-runs:
      `−0.25×2 · 0.0×6 · 0.25×7 · 0.50×3 · 1.0×0` — a genuine dense gradient. Per-instance
      modes (the 23.2 seed map): **no-tool-stop** 12481/15346 (0.0), **tool-churn**
      21627/22714 (0.25), **clean-edit-P2P-intact** 18621 (reliable **0.50** — strongest
      headroom), 21614 unstable (0.25↔−0.25). Code shipped in `harness_eval.py`
      (`gepa_t3_shaped_score`/`gepa_t3_shaped_stats`/`gepa_t3_fitness`/`gepa_t3_gate_check`,
      `gepa-t3-gate` subcommand); `make check` green, selftest covers totality + two-gate +
      two-ceiling + the swebench `episode_wall_s` timing read.
  - [x] **Expand the T3 tier to ~6.** ✓ Mined the offline-cached SWE-bench Lite corpus (300
        rows → 124 single-file/single-hunk/single-F2P candidates); prepared+verified **3 new
        sympy** fixes (**22714** gold 1/1 F2P 11/11 P2P · **18621** 1/1 15/15 · **15346** 1/1
        22/22) into a NEW 6-instance T3 set (orig 21614/12481/21627 + new). Old 3-instance
        numbers kept historical. (Fallback-to-3 not needed — 3 sourced cleanly, same repo.)
  - [x] **Add the TOTAL shaped per-instance score** ✓ `gepa_t3_shaped_score` — total over
        `made_edit`/`pass_to_pass_*`/`tool_call_rounds`/`reason`; rungs −0.25/0.0/+0.25/+0.50/
        +1.0; timeout does NOT cap a clean P2P-intact edit; oom/error = −0.25; an F2P-flip-that-
        broke-P2P falls to −0.25 (not `passed`). `make check`-green selftest incl. an
        exhaustive totality sweep (every terminal → a valid rung).
  - [x] **Rework `gepa_fitness`** → `gepa_t3_fitness`: `score = T3_shaped_mean` (no λ) with
        **T1 AND T2 both hard gates** (reject −inf if either drops below baseline). Added as a
        SIBLING of item-19's `gepa_fitness` (not a mutation) so the closed/adopted T2 path +
        its selftests keep working. Two-gate reject logic selftested.
  - [x] **Budget + gate-check.** ✓ `gepa-t3-gate` (median 256.9 s → ~2 candidates/3 h via
        `gepa_budget`); two-ceiling gate **UNLOCKED** on the 0.50 ceiling, 1.0 reported as the
        separate adopt gate. (Gate did NOT fail — climbable signal confirmed.)
- [x] **23.2 Seed the mode-targeted candidates** (a)–(d) — ✓ **DONE 2026-06-27.** First added
      an **APPEND channel**: in the full harness `system_prompt` REPLACES opencode's tuned
      default (the item-18 trap), so a new **`rules_append`** config key writes a local
      `AGENTS.md` (opencode APPENDS it), replicating item-19 cand2's `rules.content` append.
      Four terse seeds (each `rules_append`, `gepa_assert_serving_offline`-guarded, verified
      offline append-not-replace): **(a) `gepa-t3-a-engage`** anti-no-tool-stop · **(b)
      `gepa-t3-b-commit`** cap-exploration · **(c) `gepa-t3-c-verify`** restate-the-test ·
      **(d) `gepa-t3-d-cand2port`** ports cand2's adopted terse text as an APPEND (the transfer
      counter-arm). `make check` green; selftest covers the append channel.
- [x] **23.3 The GEPA run — TWO-PHASE go/no-go.** ✓ **Phase 1 ran → Phase 2 NOT unlocked.**
  - [x] **Phase 1 — N≈3 probe** over the shaped T3 fitness (baseline-identical proxy). Ran the
        seeds K=3 each: **(a) shaped 0.083 (Δ−0.069), (b) 0.097 (Δ−0.056)** — both **REGRESSED**
        vs baseline **0.153**, neither clears the spread; **(c) K=1 partial −0.083** (worst);
        **(d) UNRUN** (machine reclaimed mid-run 3×; the M1 Metal-OOM ceiling kept taking the
        MLX stack — and the run — down). **No candidate cleared spread on the 0.50 ceiling ⇒
        Phase 2 NOT unlocked, closed negative.** Mechanism (consistent across a/b/c): every
        appended rule **disturbed the one reliable near-miss** (18621 0.50→churn) and induced
        more churn/catastrophic edits rather than clean kept edits. *(Op note: `make mlx-up`
        wedged on a broken pyenv `python3` (missing gettext `libintl.8.dylib`) — only the proxy
        uses bare `python3`; fixed with a `python3`→3.12 PATH shim. See memory.)*
  - [ ] **Phase 2 — the longer run** — **NOT RUN** (Phase-1 go/no-go failed). Held for a future
        capability shift; reopen only if the shaped signal becomes climbable.
- [x] **23.4 Counter-arm + verdict.** ✓ **VERDICT: outcome (iii) — NO MOVEMENT even on the
      shaped signal; the T3 capability wall holds, now VALIDATED under shaping (not assumed).**
      The counter-arm is candidates (a)/(b) themselves (fixed candidates vs baseline, K=3): both
      regressed, so item-16/19's "prompt changes don't move the real-code rung" holds under a
      controlled shaped-reward run — and is **refined**: it's not only that *replacing* the
      prompt hurts (item 18), **appending** terse mode-targeted rules also regresses the weak 4B
      on real fixes. No adopt (nothing to re-validate). **Caveat:** 2 of 4 arms fully measured
      (a,b K=3) + c partial; **d (cand2 transfer) UNRUN** — but a/b/c all point one way, so the
      negative stands with d flagged. **The lasting deliverable is 23.1** (the shaped reward +
      two-gate fitness + `gepa-t3-gate` + the 6-instance T3 set), reusable when capability moves.
- [x] **`make check` green** + selftests for the shaped reward (totality), the two-gate
      fitness, the two-ceiling gate, the swebench `episode_wall_s` timing, and the
      `rules_append` append channel. ✓ green throughout.

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

- [x] **Update** `docs/structured-optimisation-research.md` — ✓ §23.1 (shaped reward + gate
      UNLOCKED) and §23.3 (Phase-1 probe: a/b regressed, wall holds under shaping — verdict iii).
- [x] **Update** `docs/tiered-harness.md` — ✓ "shaped T3 climbing signal" subsection.
- [x] **Update** `docs/opencode-local.md` + `CHANGELOG.md` — ✓ done (item 23 closed entry: 23.1
      machinery shipped + UNLOCKED gate; 23.3 negative — appended rules regress the weak 4B on
      real fixes too; d unrun).

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

### 24. Small-model survey — review 4–7B local models vs the Gemma-4-E4B QAT baseline  ← deep-research item

**Goal.** Decide whether **swapping the local model** (not the harness) is the lever
that finally moves the **stable 0/8 capability wall** that items 16/18/19/23 proved is
**capability-bound, not harness-bound**. Items 16–23 have exhausted the *harness-side*
levers on the frozen **Gemma-4-E4B QAT** (mlx-lm 0.31.3): the L0–L6 mechanical sweep
(item 16), the recommender (18, REJECT), GEPA on T2 (19, the one modest ADOPT) and GEPA
on T3 (23, wall holds under shaping) all hit the same ceiling. **Item 22 is the unlock
for this item:** it proved the harness is **sound** (online BigPickle 4/8 on the
identical scaffolding where Gemma scores 0/8) → the 0/8 is the *model*, so the obvious
remaining question is whether a **different small model in the same size/serving class**
clears more of the wall on **this exact harness**. This item **reviews and ranks
candidate 4–7B models**, gathers their **external coding-agent benchmarks**, and stages
them for a head-to-head local A/B against the QAT-Gemma-4 baseline.

> **⚖ Relaxes the "model is FROZEN" constraint — deliberately and narrowly.** Items
> 8–11 froze the model so that *harness* levers could be measured against a fixed
> optimisee. That programme is now substantially complete (only item 20 left), and its
> verdict is "the wall is the model". This item is the sanctioned point to re-open the
> model choice — but **only within the same deployment class**: fully-local / offline at
> serve time, **16 GB M1** (~8–12 tok/s decode, ~40–50K-token Metal-OOM ceiling),
> single-user interactive opencode against a local MLX `/v1` endpoint, repair proxy
> (`scripts/mlx_repair_proxy.py`) ON. A candidate that needs more RAM, a non-MLX engine,
> or a cloud endpoint is **out of scope** (that is the item-22 BigPickle bracket, already
> measured). **Gemma-4-E4B QAT stays the baseline every candidate is scored against.**

> **Hard operational constraint — ONE MODEL LOADED AT A TIME.** The 16 GB M1 cannot hold
> two of these models in memory simultaneously. Every candidate is **served, evaluated,
> and torn down sequentially**; the harness points at the single live MLX endpoint per
> arm. No concurrent serving, no A/B that requires two endpoints up at once. (The
> baseline numbers are *recorded once* and reused as the fixed reference — Gemma is not
> re-served alongside a candidate.)

> **Evidence policy binds this item hard.** External leaderboards / model cards / paper
> benchmarks (SWE-bench, Aider polyglot, LiveCodeBench, BigCodeBench, etc.) are a
> **ranking starting point, tagged [lit-only]** — they are run on full-precision weights,
> bigger context, and a different scaffold than ours. A model's headline SWE-bench score
> does **NOT** transfer to "passes on this 16 GB M1 / 4-bit-quant / opencode / repair-proxy
> harness". Only a **local K≥3 harness run on this machine** may adopt or reject a
> candidate. A candidate that cannot even **serve on mlx-lm within the memory ceiling** or
> **emit valid tool-calls** is recorded as a **feasibility null** (itself evidence), never
> silently skipped.

### Design decisions (resolved — plan-review 2026-06-28)

- **Candidate axis → general-but-strong, current-gen only; shortlist = Qwen3.5-4B then
  Qwen3.5-9B.** The 24.1 v2 survey settled this: every coding-specialised candidate is
  superseded (Qwen2.5-Coder-7B, Qwen3-4B) or off-budget (Qwen3-Coder-Next 44.8 GB), so the
  funded A/B is the two **Qwen3.5 small-dense** models [2026-03-02] against the recorded
  **Gemma-4-E4B QAT** [2026-06-05] baseline. *Rationale:* same size/serving class, confirmed
  MLX 4-bit builds, newest small line. **Phi-4-Mini is NOT a funded arm** — recorded maybe/
  null only (no confirmed MLX build, no agent/tool-calling data to rank it). *(user-confirmed
  2026-06-28, Q1/Q3.)*
- **Quantisation parity → best-fit MLX quant per model, bit-width is a recorded covariate
  (NOT held constant).** Each candidate runs at its best-fitting MLX quant under the 16 GB
  ceiling; effective bpw is logged alongside tok/s + peak RAM. The **QAT(Gemma)-vs-PTQ/AWQ/
  mixed(candidates)** method difference is accepted as recorded — and **any negative verdict
  carries an explicit quant-method-confound flag** (a candidate losing to the QAT baseline
  may be losing to *quant method*, not architecture). *(user-confirmed 2026-06-28, Q4.)*
- **Scoring regime → CONFIRMED: reuse the item-17 tiered harness unchanged.** T1/T2 micro +
  the item-23 6-instance shaped-T3 reward + full pass/8, the **exact same rungs** as every
  prior item, single lever = the served model. Primary adopt signal = a candidate moves
  **full pass/8 above 0** (the wall) where Gemma cannot; secondary = shaped-T3 mean (+ spread)
  + the T1/T2 micro gradient. No new scoring code — the served model is auto-detected by
  `harness_eval.py` (`detect_model(base_url)` → `mlx-local/<served-path>`), so a candidate is
  selected at the **serve layer** (`MLX_MODEL=… make mlx-up`), not via a config model field.
- **Engine scope → mlx-lm 0.31.3 FIRST; `mlx_vlm` is a GATED FALLBACK, not stood up up front.**
  The Qwen3.5 small models are natively multimodal and their 4-bit builds may require the
  separate `mlx_vlm` engine (`mlx.sh` serves only `uvx --from mlx-lm==0.31.3 mlx_lm.server`).
  Ruling: try each candidate on the **frozen mlx-lm 0.31.3** path first; a candidate that will
  not load there is a **recorded feasibility null**. ONLY if **both** Qwen3.5 arms null on
  mlx-lm do we spend a separate `mlx_vlm.server` bring-up as explicit follow-up work — the
  serving substrate stays constant by default. *(user-confirmed 2026-06-28, Q1.)*
- **Repair-proxy mode → PASSTHROUGH for non-Gemma; the literal "proxy stays ON" is
  reinterpreted.** `mlx_repair_proxy.py` is Gemma-4-token-specific (ports `gemma4.py` —
  `<|tool_call>` / `<|"|>` / Gemma `enable_thinking` kwarg), so its repair logic is a no-op
  (or a confound) on a Qwen/Phi candidate. For every non-Gemma candidate the proxy stays
  **in path for tracing but with `MLX_PROXY_REPAIR=0` (repair OFF / passthrough)** — tool-call
  validity is measured on the model's **own native mlx-lm parser output**, apples-to-apples.
  IF a candidate shows a *systematic, mechanically-fixable* tool-call defect in the 24.2
  smoke, a **per-model repair shim** is written before its scored run (keeps the tool-call
  floor honest at extra build cost). The original item's "repair proxy stays ON" constraint is
  hereby read as **"proxy in path, repair-mode passthrough for non-Gemma"**, NOT repair-on.
  *(user-confirmed 2026-06-28, Q2.)*
- **Feasibility gate (build-time, cheap) → per candidate, before any scored run:** (a) serves
  on **mlx-lm 0.31.3** within the OOM ceiling (mlx_vlm only as the gated fallback above),
  (b) emits valid tool-calls through the proxy **in passthrough** on a 1–2 instance smoke
  check. Fail (a) or (b) ⇒ recorded null arm (itself evidence, per the Evidence policy).
- **Budget / sequencing → FULL up front, 2 candidates × K≥3, with a wall-clock ceiling.** Run
  the full battery (full pass/8 + shaped-T3 + T1/T2 micro) at **K≥3** on **both** Qwen3.5 arms
  from the start — no cheap-micro-gate-first sequencing — for the most complete per-arm
  evidence. One model loaded at a time (sequential serve/eval/teardown). Keep a **per-candidate
  wall-clock ceiling** (item-23 `gepa_budget` pattern) + abort discipline, and treat the
  **Qwen3.5-9B as an explicit OOM risk** at the 40–50K-token ceiling (~5.6 GB weights + KV +
  any thinking-mode blowup — record a feasibility null if it OOMs rather than chasing it).
  *(user-confirmed 2026-06-28, Q3.)*

- [ ] **24.1 Deep-research survey — DELIVERED 2026-06-27, REFRESHED 2026-06-28.** External
      benchmarks for small (≈4–7B) local coding-agent models gathered, fact-checked, and
      synthesised into a ranked shortlist with MLX-availability + 16 GB-fit notes.
      Reports: v1 **`docs/small-model-selection-research.md`** (2026-06-27) and the
      latest-release **v2 `docs/small-model-selection-research-v2.md`** (2026-06-28, current).
      **v2 supersedes v1's shortlist:** the field turned over generationally — **all four
      v1 picks are now superseded or off-budget** (Qwen2.5-Coder-7B & Qwen3-4B → superseded
      by the **Qwen3.5 small series** released 2026-03-02; Yi-Coder-9B / xLAM-2-3b-fc-r not
      re-confirmed). Gemma 3 correctly excluded — incumbent is **Gemma 4** (Gemma-4-E4B QAT
      released 2026-06-05, supersedes Gemma 3, valid current-gen baseline). **Refreshed
      shortlist (release dates recorded per candidate):** (1) Qwen3.5-9B [2026-03-02],
      (2) Qwen3.5-4B [2026-03-02], baseline Gemma-4-E4B QAT [2026-06-05], Phi-4-Mini a weak
      maybe. Verdict tagged **[lit-only]** per the Evidence policy — a ranking, not an
      adoption; 24.3 is its local validation. **Two 24.2 gate items surfaced:** (a) NO
      external multi-turn tool-calling benchmark survived verification for ANY candidate (the
      binding dimension is literature-blind → only 24.3 decides it); (b) Qwen3.5 small models
      are natively MULTIMODAL → 4-bit builds may load via `mlx_vlm` not `mlx_lm` (repair-proxy
      / opencode integration check before A/B).
- [x] **24.2 Shortlist + feasibility staging (NO scored run).** Shortlist resolved by
      plan-review = **Qwen3.5-4B then Qwen3.5-9B** (Phi-4-Mini not funded). For each: confirm
      an mlx-lm-loadable build at its **best-fitting quant** under the 16 GB ceiling (record
      effective bpw), then **stage a per-candidate SERVE RECIPE — not just a config JSON**.
      The model is selected at the serve layer (`MLX_MODEL=…`/`MLX_REVISION=…` + `make
      mlx-pull` of the pinned revision into `mlx-models/`), and the harness auto-detects the
      served id (`detect_model` → `mlx-local/<path>`), so the deliverable per candidate is:
      (i) the env + pull + **revision pin** recipe, (ii) a `scripts/harness_configs/model-<name>.json`
      carrying sampling/limits/rules only, and (iii) the **proxy run in passthrough**
      (`MLX_PROXY_REPAIR=0`) for these non-Gemma models. Feasibility gate (cheap):
      (a) **serves on mlx-lm 0.31.3** within the OOM ceiling — a model needing `mlx_vlm` is a
      recorded **null on the mlx-lm path** (mlx_vlm bring-up only if BOTH Qwen3.5 arms null);
      (b) emits valid tool-calls through the **passthrough** proxy on a 1–2 instance smoke. If
      a candidate shows a systematic, mechanically-fixable tool-call defect, write a per-model
      repair shim before 24.3. Fail (a)/(b) ⇒ recorded null arm.
  **✓ DONE 2026-06-28 — BOTH candidates PASS the feasibility gate** (`docs/item24-feasibility-notes.md`).
  Deliverables shipped: `scripts/harness_configs/model-qwen3.5-4b.json` + `model-qwen3.5-9b.json`
  (sampling/rules only), `scripts/harness_configs/SERVE-RECIPES-item24.md` (env/pull/revision-pin
  + passthrough ruling + the python3.12 proxy shim). Weights pulled (4B 2.9 GB rev `32f3e8e…`;
  9B 5.6 GB rev `938d891…`). **Key results:** (1) the survey's `mlx_vlm` risk is **RETIRED** —
  both are multimodal `Qwen3_5ForConditionalGeneration` but mlx-lm 0.31.3's `qwen3_5` module
  `sanitize()` strips vision and serves **text-only**; no fallback engine, no repair shim
  (native Qwen tool-call parser emits clean OpenAI `tool_calls` through the passthrough proxy).
  (2) **4B PASS** — serves, valid tool-calls, engaged 12 steps/240 s, no OOM. (3) **9B PASS but
  slow** — serves/valid tool-calls/no-OOM-at-tier-4, but thinking-mode made step 0 alone take
  **204 s** (~half the 4B decode speed). **NEW 24.3 design fork surfaced:** Qwen3.5 ships
  **thinking-mode ON by default** (155 reasoning tokens on a trivial prompt vs 6 with
  `enable_thinking=false`) — material for the 4B, near-blocking for the 9B on wall-clock.
  Decide thinking ON vs OFF (record as a covariate) before the scored run. (Smoke `--timeout`
  240/300 s were deliberately tight; 24.3 uses the 600 s default.)
- [ ] **24.3 Local-harness A/B — the actual evidence.** Serve each shortlisted candidate
      **sequentially** (one model loaded at a time, proxy in passthrough for non-Gemma),
      evaluate the **FULL battery up front** (full pass/8 + item-23 shaped-T3 + T1/T2 micro)
      at **K≥3** vs the **recorded Gemma-4-E4B QAT baseline**, with a per-candidate wall-clock
      ceiling (item-23 `gepa_budget`) + abort discipline. **Qwen3.5-9B is an explicit OOM risk**
      at the 40–50K-token ceiling — record a feasibility null if it OOMs rather than chasing it.
      Adopt/reject from the **local numbers**; tag any negative verdict with the **quant-method
      confound** caveat (QAT baseline vs PTQ/AWQ candidate). **Valid outcomes (all closed, per
      Evidence policy):**
      (i) a candidate moves full pass/8 above 0 (or clears the shaped-T3 spread) → a
      **model swap is the lever**, recommend it; (ii) candidates move the micro/shaped
      gradient but not pass/8 → **partial**, record the rung; (iii) no candidate beats
      Gemma on this harness → the 4–7B class is a wall *here* and BigPickle-class (item 22)
      is the only thing that clears it — a valid closed negative that re-justifies the
      original frozen-Gemma choice.
      **— 4B ARM DONE 2026-06-29** (label `qwen35-4b-K3-serialized`, K=3×11, OOM-safe serialized
      relaunch after the first attempt OOM'd the 4B on unbounded KV-cache; fix = mlx-server cache
      caps + model-guard + py-shim, see `docs/item24-feasibility-notes.md`). **Result 0.3/11
      (spread 0–1) → does NOT clear spread → not distinguishable from Gemma 0/8 ⇒ tracking (iii)
      for the 4B** (quant-confound caveat applies). Notable: failure mode is **timeout-bound, not
      no-edit** (29/33 timeout; engages+edits, `dropped`=0, `made_edit`=0.30) — Qwen3.5-4B is
      wall-clock-bound on the 16 GB M1, a *different* wall than Gemma's no-tool-stop.
      **9B arm still TODO** (reuse the serialized driver w/ 9B MLX_MODEL/REVISION). 24.3 closes
      after the 9B arm; final verdict pending.

### Measurement plan (item 24)

- **Baseline:** the recorded Gemma-4-E4B QAT numbers (reuse the item-17/23 baseline
  ledgers; do not re-serve Gemma alongside a candidate).
- **The single lever varied:** the **served model** (Gemma → candidate). Harness, tiers,
  shaped reward, sampling defaults held fixed across arms. **Proxy nuance (NOT a held
  constant byte-for-byte):** the proxy stays in path for tracing on every arm, but its
  **repair-mode differs by necessity** — Gemma baseline = repair ON (its #1096/#1125 fix),
  non-Gemma candidates = `MLX_PROXY_REPAIR=0` passthrough (Gemma's parser doesn't apply).
  This is a recorded, intrinsic-to-the-model difference, not an uncontrolled lever; tool-call
  validity is measured on each model's native parser output.
- **Per-candidate metrics:** full pass/8, shaped-T3 mean (+ spread), T1/T2 micro fracs,
  made-edit / tool-call-validity rates, tok/s + peak RAM (the deployment-fit covariates),
  and the quant bit-width used. K≥3 mean + spread per candidate.
- **Gate:** `make check` green for any harness code touched; `gepa_assert_serving_offline`
  (or equivalent) asserts each candidate config stays on a local MLX endpoint.

### Documentation (item 24)

- [ ] **Add** `docs/small-model-selection-research.md` (v1, 2026-06-27) **and**
      `docs/small-model-selection-research-v2.md` (v2, 2026-06-28, current) — the 24.1
      survey: ranked candidates, external benchmarks with citations, MLX/quant/16 GB-fit
      notes, per-candidate release dates, the [lit-only] tag. **v2 is the live shortlist;
      v1 is retained for history.**
- [x] **Added** `docs/item24-feasibility-notes.md` (2026-06-28) — the 24.2 build-time
      feasibility-gate results MEASURED on this machine: `mlx_vlm`-risk-retired loader finding,
      per-candidate serve/tool-call/engagement/OOM table, and the thinking-mode-default finding
      that becomes the open 24.3 design fork.
- [ ] **Update** `docs/opencode-local.md` (master doc) — record item 24's adopt/reject
      outcome (model swap: adopted / partial / rejected) once 24.3 closes.
- [ ] **Update** `CHANGELOG.md` only when item 24 closes (item-17/19/21 pattern).

### 25. GEPA-optimise the multi-agent PLANNING prompt  ← follows item 20

**Goal.** Item 20 closed (ii) PARTIAL with a tantalising thread: the **multi-agent arm
(c)** was the **only** arm that ever landed a real T3 fix (`sympy-22714`, the correct
`evaluate`-guard `point.py` edit, **4/6 over K=6**) — but the win **did not survive
re-validation** and is **mechanism-incidental**: the `task` tool **never fires**, and the
gain appears to come from the **`planner`/`coder` subagent DESCRIPTIONS** sitting in
context as an *accidental goal-style plan scaffold* (not working orchestration). This item
asks: **if a side-effect of unoptimised planner text already cracks a real fix, can
deliberately OPTIMISING that planning prompt turn the fragile, incidental win into a robust
one** — and combine item-19's **GEPA** machinery with item-20's multi-agent topology so the
planner prompt is *evolved against the shaped-T3 signal* rather than hand-written.

> **Two-stage structure (online-first, plan-review 2026-06-28).** The item now runs in
> **two stages**: **Stage 1 (Phase 25.1) is an ONLINE feasibility probe on BigPickle** — run
> the GEPA-on-planning loop against the **capable** online model FIRST, to separate *"does
> GEPA-on-planning work MECHANICALLY at all"* from *"does it transfer to the weak local 4B"*,
> **before** spending the expensive/OOM-bound local Gemma budget. **Stage 2 (Phases
> 25.2–25.4) is the local Gemma run**, gated behind Stage 1's greenlight. *Rationale:* item 20
> showed the local arm-c win is fragile and OOM-bound; if optimising the planner prompt cannot
> even make a *capable* model's `task` tool fire and move the signal, there is no point burning
> hours of local OOM-bound rollouts to prove it on the 4B.

> **Builds directly on shipped machinery (item 19 + item 20 + item 22/27 + item 23).**
> Stage 2 reuses the item-19.3 GEPA loop wiring (Opus-4.8 **in-loop reflector**, frozen local
> Gemma as the optimisee + evaluator, `gepa_assert_serving_offline` on every candidate). **Stage 1
> reuses item-27.1's shipped online-optimisee machinery** (`gepa_assert_online_optimisee` + the
> `gepa_assert_optimisee_mode` dispatcher wired into `cmd_run` — `harness_eval.py:1617,1658`;
> the **1.0 online unlock-ceiling** via `gepa_t3_gate_check(..., online=True)` — `harness_eval.py:1565`;
> the online `gepa_budget` latency/retry dimension) **plus item-22's `external_provider` path +
> `online-bigpickle.json`**. Both stages use the item-23 **shaped-T3 reward** as the fitness
> signal (`gepa_t3_shaped_score`, the `gepa-t3-gate`) on the **same 6-instance T3 set**. The
> optimised text is the **`planner`/`coder` subagent prompts/descriptions + the orchestration
> `rules_append`** — all **APPEND/sub-agent text levers**, never the `system_prompt`/
> `agent.build.prompt` REPLACE channel (item-18 suppression). **In BOTH stages the GEPA reflector
> (Opus-4.8) lives only in the optimisation loop; in Stage 2 serving stays local-offline, in
> Stage 1 the OPTIMISEE is online (BigPickle) while the local MLX stack is off.**

> **Evidence policy.** GEPA-on-planning is a **hypothesis [lit-only/tool-proposed]** until a
> local K≥3 A/B closes it. **Stage 1's online result is a feasibility/soundness probe, tagged
> [online-probe] — it NEVER adopts anything into the shipped local harness; only the local
> re-val (25.4) governs adoption.** Valid outcomes (all closed): (i) the LOCAL optimised planner
> prompt clears the 19.2/23.1 spread test on the shaped signal **AND survives an independent
> re-val** (the bar item 20's arm c failed) → **adopt**; (ii) it moves the shaped mean but no
> robust binary flip → partial; (iii) no movement → planning-prompt optimisation does not
> transfer to the 4B, a valid closed negative (the multi-agent win stays incidental/unrobust).
> A FOURTH valid closed outcome is **Stage 1 itself failing the greenlight gate** → close item 25
> early (planning-prompt optimisation is mechanism-inert / signal-flat even on a capable model)
> WITHOUT spending the local budget.

**Open questions this item must settle:**
- Does an evolved planner prompt make the `task` tool **actually fire** (real delegation), or
  does the benefit stay a context-scaffold side-effect even when optimised? *(Answered first on
  the capable model in Stage 1 — far more likely to fire there than on the weak 4B.)*
- Can it convert 22714's **4/6** into a **robust** flip (survives re-val), and/or crack a
  **second** T3 instance (generalisation beyond the one lucky instance)?
- Does it do so **without** re-introducing cand2's OOM-churn regression (the planning text
  must not make the weak 4B churn into the 16 GB wall — item-20's failure mode)?
- **Does the OPTIMAL planner prompt TRANSFER across models?** BigPickle's optimal planner text
  is NOT assumed to be Gemma's — it is a per-model artifact. Stage 2 explicitly *verifies*
  whether seeding Gemma's GEPA from BigPickle's optimal prompt beats Gemma optimising from the
  arm-c base / from scratch.

### Design decisions (resolved — plan-review 2026-06-28)

- **Online-first staging → Stage 1 is a BigPickle feasibility probe that GATES Stage 2.** Run
  GEPA-on-planning against the capable online optimisee first (`opencode/big-pickle`, item 22's
  `external_provider` path). *Rationale:* cheap-and-fast (240s cap, fast gateway) vs the local
  ~455 s/OOM-bound T3 rollouts; it isolates "does optimising the planner prompt work mechanically
  at all" from "does it transfer to the 4B". A Stage-1 failure closes item 25 before any local
  budget is spent. *(user-confirmed 2026-06-28, Q1/Q2.)*
- **Greenlight gate = BOTH signals required (strictest).** Stage 1 greenlights Stage 2 **iff**
  (a) the `task` tool **ACTUALLY FIRES** (real delegation, not the inert context-scaffold side-
  effect item 20 saw) **AND** (b) the **shaped-T3 mean / F2P signal MOVES** on BigPickle (clears
  the spread test at the **1.0 online ceiling**, or a robust F2P flip). If EITHER fails
  (mechanism inert OR signal flat on a capable model) → **close item 25 without spending the
  local Gemma budget.** *(user-confirmed 2026-06-28, Q2.)*
- **Baseline → same 6-instance T3 set, with a NEW BigPickle-on-`plan-arm-c-multiagent` baseline
  measured INSIDE Stage 1.** The 6-instance tier-3 set (21614/12481/21627/22714/18621/15346,
  `harness_eval_subset.json`) is held across both stages for comparability. ⚠ **Item 22's 4/8 is
  NOT this baseline** — it was the mixed 8-instance T3+T4 subset on the *bare* config at the 240s
  cap. Stage 1 measures its OWN BigPickle-on-arm-c-multiagent baseline (online mode, ceiling 1.0);
  it does **not** reuse item 27.2's (generic, non-arm-c) baseline and does **not** block on it.
  *(user-confirmed 2026-06-28, Q3.)*
- **Item-27 relationship → REUSE 27.1's machinery, run INDEPENDENTLY of 27.2–27.4.** Stage 1 is
  planning-specific (arm-c topology + planner/coder text) and rides item-27.1's shipped online-
  optimisee path (`gepa_assert_online_optimisee`, mode dispatcher, 1.0 ceiling, online budget —
  all DONE). It does NOT depend on item 27.2/27.3/27.4. The overlap (both run GEPA against
  BigPickle) is noted in docs so neither double-pays, but item 25 measures its own arm-c baseline.
  *(user-confirmed 2026-06-28, Q4.)*
- **Online result status → [online-probe], NEVER a ship/adopt.** A positive Stage-1 result can
  greenlight Stage 2 and **seed the local reflector** (BigPickle's optimal planner text becomes an
  *input* to Stage 2), but it adopts NOTHING into the shipped local harness. Only the local
  re-val (25.4) governs adoption. Tag every Stage-1 finding **[online-probe]**. *(user-confirmed
  2026-06-28, Q5.)*
- **Counter-arm in Stage 1 (validates the negative on a capable model).** Build a **fixed naive
  planner edit** arm vs the arm-c base on BigPickle, so a "GEPA-on-planning doesn't help even a
  capable model" negative is **measured, not assumed** (Evidence policy). *(user-confirmed
  2026-06-28, Q6.)*
- **Per-model planner-prompt store + EXPLICIT Gemma transfer-verification (supersedes naive
  transfer).** Maintain **separate optimal planner prompts PER MODEL** (a BigPickle-optimal text
  AND a Gemma-optimal text). Gemma does **NOT** inherit BigPickle's prompt directly — it may use
  it as an INPUT/seed, but **whether the planner prompt transfers must be explicitly VERIFIED on
  the local Gemma**. Stage 2 includes a transfer-verification arm: *does seeding Gemma's GEPA from
  BigPickle's optimal prompt beat Gemma optimising from the arm-c base / from scratch?* The
  adopted Gemma lever is whichever Gemma-local arm survives re-val — never the BigPickle text
  unverified. *(user-confirmed 2026-06-28, Q7.)*
- **arm-c needs an online variant config.** `plan-arm-c-multiagent` re-enables `task` + defines
  `planner`/`coder` subagents via raw `opencode_config` (no `mlx-local` block). Stage 1 needs an
  `external_provider`+`model_ref: opencode/big-pickle` variant (e.g. `plan-arm-c-multiagent-online`)
  that passes `gepa_assert_online_optimisee` (no local-serve leak). The `task`/subagent machinery
  is opencode-side (provider-agnostic) → a build-time feasibility smoke confirms BigPickle emits
  valid tool-calls AND that `task` can fire under the online provider before the GEPA loop runs.

#### Stage 1 — ONLINE BigPickle feasibility probe (gates Stage 2)  [online-probe]

- [ ] **25.1a Build the online arm-c config + feasibility smoke.** Add
      `scripts/harness_configs/plan-arm-c-multiagent-online.json` (= `plan-arm-c-multiagent`
      base + `external_provider: true` + `model_ref: opencode/big-pickle` + the provider-
      appropriate sampling/timeout from `online-bigpickle.json`); assert it passes
      `gepa_assert_online_optimisee` (no `mlx-local`/local-`baseURL` leak). Build-time smoke on
      one instance: BigPickle emits valid structured tool-calls AND the `task` tool CAN fire
      under the online provider (a precondition for the greenlight gate). A failed smoke = a
      recorded wall-confirming null, not a silent skip.
- [ ] **25.1b Measure the BigPickle-on-arm-c baseline + budget the run.** K≥3 baseline of
      BigPickle's shaped-T3 mean + spread on the 6-instance T3 set under
      `plan-arm-c-multiagent-online` (this is the item-25-OWN baseline; do NOT reuse 27.2's
      generic one). Apply the unlock rule at **ceiling = 1.0** (online mode). Budget from
      measured online **latency + rate-limit/retry** (`gepa_budget` online dimension; token-cost
      recorded-as-zero for the free default). Abort→close-as-negative if no climbable headroom.
- [ ] **25.1c Run GEPA-on-planning against the ONLINE optimisee + counter-arm.** Opus-4.8 in-loop
      reflector evolves the `planner`/`coder` subagent prompts + orchestration `rules_append`;
      evaluate each candidate K≥3 against BigPickle with `gepa_t3_shaped_score`;
      `gepa_assert_online_optimisee` on every candidate via `cmd_run`. **Counter-arm:** a fixed
      naive planner edit vs the arm-c base. Record whether **`task` actually fires** per candidate.
      Output: **BigPickle's optimal planner prompt** stored as a per-model artifact (e.g.
      `scripts/harness_configs/plan-arm-c-bigpickle-optimal.json`), tagged **[online-probe]**.
- [ ] **25.1d GREENLIGHT DECISION (the gate).** Greenlight Stage 2 **iff** (a) `task` actually
      fired under the evolved prompt **AND** (b) the shaped/F2P signal moved past spread at
      ceiling 1.0. **If either fails → CLOSE item 25 here** (planning-prompt optimisation is
      mechanism-inert or signal-flat even on a capable model — a valid closed negative, no local
      Gemma budget spent). Record the verdict + both signals in the ledger `notes`.

#### Stage 2 — LOCAL Gemma run (only if Stage 1 greenlights)

- [ ] **25.2 Local feasibility gate (mirror 19.2/23.1).** Confirm the shaped-T3 signal on the
      `plan-arm-c-multiagent` base still shows climbable headroom > spread (re-read the K=6
      arm-c ledger; no new run), and budget the local GEPA run from the measured ~455 s/T3-rollout
      (`gepa_budget`). Abort→fallback if unconverged. `gepa_assert_serving_offline` on every arm.
- [ ] **25.3 Local GEPA loop over the planner/orchestration text + TRANSFER-VERIFICATION.**
      Opus-4.8 in-loop reflector proposes edits to the `planner`/`coder` subagent prompts +
      orchestration `rules_append`; evaluate each candidate K≥3 on the 6-instance T3 set with
      `gepa_t3_shaped_score`; T1/T2 hard gates + tool-call-validity floor hold;
      `gepa_assert_serving_offline` on every arm. **Run TWO Gemma optimisation arms and compare:**
      (i) Gemma GEPA from the arm-c base (from scratch), and (ii) Gemma GEPA **seeded from
      BigPickle's optimal planner prompt** (Stage 1 output as an input, NOT adopted directly).
      The transfer question = does seeding help vs from-scratch? Produce a **Gemma-optimal planner
      prompt** as its own per-model artifact (distinct from BigPickle's).
- [ ] **25.4 Adopt only if it SURVIVES re-validation** — independent K≥3 re-run (reflector out of
      the eval path), the win within one spread of the in-loop score AND a robust binary flip
      (the explicit bar item-20 arm c missed), tool-calls valid, no OOM-churn regression. The
      adopted lever is the **Gemma-local** winner (whichever of from-scratch / seeded survives) —
      never BigPickle's text unverified. Counter-arm: a fixed naive planner edit, to keep the
      negative honest.
- [ ] `make check` (ruff + mypy + pytest/selftest) green for any harness code touched
      (new online arm-c config loader / feasibility-smoke / per-model artifact handling).

### Measurement plan (item 25)
- **Stage 1 (online, [online-probe]):** BigPickle shaped-T3 mean (K≥3) on the 6 T3 instances at
  **ceiling 1.0** + a `task`-fired flag per candidate. **Greenlight gate = BOTH** task-fires AND
  signal-moves-past-spread; fail either → close item 25. Own arm-c baseline measured in 25.1b;
  counter-arm = fixed naive planner edit. Online cost dims (latency, rate-limit/retry; token-cost
  =0 for the free default). Guard: `gepa_assert_online_optimisee` via `cmd_run` on every candidate.
- **Stage 2 (local, governs adoption):** item-23 shaped T3 mean (K≥3) over the 6 T3 instances;
  **adopt gate:** a binary F2P flip that **survives an independent re-val** + tool-calls valid +
  no OOM-churn regression. The single lever varied = the **planner/orchestration TEXT** (topology
  fixed at arm-c multi-agent). Baselines: item-20's `plan-arm-c-multiagent` + bare. **Transfer
  arm:** Gemma-from-scratch vs Gemma-seeded-from-BigPickle-optimal. Guard:
  `gepa_assert_serving_offline` on every candidate.
- **Per-model artifact store:** a BigPickle-optimal planner prompt (Stage 1) AND a Gemma-optimal
  planner prompt (Stage 2) — kept SEPARATE; transfer is verified, never assumed.
- **Gate:** the mode-selected guard enforced in `cmd_run` on every candidate; `make check` green.

### Documentation (item 25)
- [ ] **Update** `docs/structured-optimisation-research.md` — a §25 extending the GEPA write-up
      to the multi-agent planning prompt (combines item 19 + 20 + 27), documenting the online-first
      staging, the both-signals greenlight gate, and the per-model prompt store + transfer-
      verification result.
- [ ] **Update** `docs/orchestration-planning-research.md` — record whether optimising the
      planner prompt makes the multi-agent `task` mechanism FIRE (Stage 1 on BigPickle vs Stage 2
      on Gemma) / robustifies the 22714 win, and whether the optimal prompt transfers across models.
- [ ] **Update** `docs/opencode-local.md` + `CHANGELOG.md` only when item 25 closes.

### 26. Evaluate codebase-exploration tools (codegraph-class) for planning  ← deep-research + local-eval item

**Goal.** Item 20 located the real bottleneck on this stack: the weak 4B **churns grep/read**
into longer contexts until it hits the **16 GB / OOM wall** (cand2's T3 regression; 22714's
OOM/decode variance), and its planning is poorly grounded in repo structure. This item asks
whether **structure-aware codebase-exploration tools — codegraph-class** (code-graph / call-graph
indexers, tree-sitter/AST code maps, LSP/`ctags` symbol indexes, repo-map tools à la aider's
repomap, or an MCP code-graph server) — give the agent **denser structural grounding in fewer
tokens**, reducing the explore-churn and improving the **plan** quality (and thus the shaped-T3
signal). Net: does cheaper, structure-aware exploration help PLANNING and lift real fixes?

> **Hard constraints carry through (items 8–11).** Fully **local / offline at serve time**, **16
> GB M1**, model + serving engine **FROZEN**. Any candidate tool must run **locally and offline**
> (a local index/graph build is fine; no network at serve time) and integrate as an **opencode
> tool** (a `.opencode/tools/*.ts` shadow tool or a local MCP server) so it rides the existing
> harness lever path. A tool that needs the cloud or blows the memory budget is out.

> **Evidence policy.** Every "tool X helps planning" claim is **[lit-only]** from the survey
> until a **local K≥3 A/B** on the shaped-T3 set closes it. The survey *ranks*; only a
> harness run *adopts*. Tie the win/no-win to the same shaped-T3 reward items 20/23/25 use, so
> outcomes are comparable across items.

**Open questions this item must settle:**
- Which codegraph-class tools are **local-offline + 16 GB-feasible** and exposable as an
  opencode/MCP tool? (codegraph, tree-sitter code-map, ctags/LSP symbol index, aider-style
  repomap, sourcegraph-local, etc. — survey + feasibility-screen.)
- Does giving the agent a **structural map / symbol lookup** instead of raw grep/read **reduce
  tool-call rounds + output tokens + OOM rate** on the T3 set (the item-20 churn metrics)?
- Does it **improve the PLAN** specifically — fewer wrong-file edits, faster commit-to-edit,
  a higher shaped mean — vs the cand2/grep-discipline baseline that *regressed* T3?
- Does it **stack with item 25** (a structure-grounded planner prompt) for a combined lift?

- [ ] **26.1 Deep-research survey + feasibility screen.** Survey codegraph-class tools; for each
      record: local/offline?, 16 GB build/serve cost, opencode/MCP integratability, and the
      claimed planning/exploration benefit (cite). Tag everything **[lit-only]**. Output:
      `docs/codebase-exploration-tools-research.md` with a ranked, feasibility-screened shortlist.
- [ ] **26.2 Wire the top 1–2 feasible tools as a harness lever.** A local `.opencode/tools/*.ts`
      shadow tool (or local MCP) exposing the code-graph/symbol-map; build the index offline;
      add `scripts/harness_configs/*.json` arm(s) that enable it (text/tool lever only;
      `gepa_assert_serving_offline` passes). Build-time feasibility smoke (does Gemma call the
      new tool + does it fit memory) — a failed smoke = recorded wall-confirming null, not abort.
- [ ] **26.3 Local A/B on the shaped-T3 set, K≥3.** Score vs bare + the grep/read baseline with
      `gepa_t3_shaped_score` + the item-20 churn metrics (tool_call_rounds, output tokens, OOM
      rate). Adopt iff it lifts the shaped mean past spread (survives re-val) OR materially cuts
      churn/OOM without regressing fixes. Valid outcomes (all closed): adopt / partial / negative.
- [ ] `make check` green for any harness/tool code touched.

### Measurement plan (item 26)
- **Primary:** item-23 shaped T3 mean (K≥3) over the 6 T3 instances. **Secondary (the churn
  thesis):** tool_call_rounds, output tokens/episode, OOM rate, steps-to-first-edit — does the
  structural tool cut the explore-churn that drives the 16 GB wall? Single lever varied = the
  **exploration tool** (grep/read → codegraph-class). Baselines: bare + cand2 grep-discipline.
- **Gate:** local/offline + 16 GB-fit asserted; `gepa_assert_serving_offline`; `make check` green.

### Documentation (item 26)
- [ ] **Add** `docs/codebase-exploration-tools-research.md` — the 26.1 survey (ranked,
      feasibility-screened, cited, [lit-only]).
- [ ] **Update** `docs/tiered-harness.md` — note item 26 reuses the shaped-T3 + churn metrics.
- [ ] **Update** `docs/opencode-local.md` + `CHANGELOG.md` only when item 26 closes.

### 27. Extend GEPA to optimise ONLINE optimisee models  ← generalises items 19/23/25

**Goal.** Today the GEPA machinery (items 19/23/25) is **hard-wired to the frozen local
Gemma as the optimisee**: `gepa_assert_serving_offline` (`harness_eval.py:1581`) *rejects*
any candidate that flips `external_provider`/`model_ref`/`base_url`, so the model the
harness **evaluates** can only ever be the offline 4B. This item adds the capability to
run the **same GEPA loop with an ONLINE model as the optimisee** — i.e. evolve prompt/text
levers *against a strong hosted model's* shaped-T3 / T2 signal, not just the weak local one.
**`opencode/big-pickle` is the worked example** (item 22 already proved the harness can
evaluate it end-to-end — 4/8 on the frozen T3 subset, scorer reads real pytest results),
but the online optimisee must be **configurable to any online `model_ref`** (other opencode-zen
models, or any `external_provider` ref), with BigPickle as the default.

> **Why this is worth doing.** Every GEPA result so far is entangled with the **16 GB / 4B
> capability wall**: item 19 found prompt *length* dominates a weak model; items 20/23 found
> the T3 wall holds under shaping; item 18 found gutting the prompt suppresses tool use. We
> have **never measured whether GEPA's text-lever optimisation generalises to a *capable*
> model** — is "terse helps / verbose hurts" a 4B artifact, or a real harness property? An
> online optimisee with genuine T3 headroom (BigPickle lands real fixes) is the control that
> answers it: it tells us whether the optimised text levers we ship are **model-specific** or
> **transferable**, and gives a second, capability-unconstrained fitness surface for GEPA to
> climb. It also makes the GEPA tooling **reusable** beyond this one frozen stack.

> **Builds on shipped machinery (item 19 + item 22).** Reuse the item-19.3 GEPA loop
> (Opus-4.8 in-loop reflector, shaped/T2 fitness, K≥3 + re-val discipline, λ floor + T1 hard
> gate) and item-22's **`external_provider` path** (`apply_levers` writes no `mlx-local`
> block; `cmd_run` skips `server_healthy`/`detect_model`/OOM-probe; `online_preflight`
> auth+network check replaces the MLX health-check — `harness_eval.py:306,414,990`). The new
> work is **decoupling the optimisee model from the serve-offline assumption**, cleanly and
> opt-in.

> **Constraint reframing (explicit, not hidden).** Items 8–11's "fully local / offline at
> serve time" constraint binds the **shipping local harness**, NOT this item. Online-GEPA is
> an **analysis/optimisation-loop capability** (like the cloud reflector already is) — it
> **never touches the frozen local serve path**. So `gepa_assert_serving_offline` is NOT
> deleted; it stays the default and the guard for local-optimisee runs. Online-GEPA is a
> **separate, explicitly opted-in mode** that swaps the offline assertion for item-22's
> `online_preflight` + a pinned-`model_ref` assertion. The two modes must not silently mix
> (a local-optimisee run that smuggles `external_provider` still fails loudly).

> **Evidence policy.** "GEPA generalises to online models" is a **hypothesis [lit-only]**
> until a local-driven A/B closes it. Valid outcomes (all closed): (i) an evolved candidate
> clears the spread test on the online optimisee's shaped/T2 signal **and survives re-val** →
> the loop works for online models (and we learn whether the winning text matches or differs
> from the local cand2); (ii) it moves the signal but no robust win → partial; (iii) no
> movement → GEPA's text levers don't help a capable model either (a valid closed negative
> that *strengthens* the "capability, not prompt" framing).

**Open questions this item must settle:**
- Does the optimal text lever for a **capable** optimisee match item-19's cand2 (terse
  wins), or does a strong model prefer richer guidance (i.e. is "terse helps" a 4B artifact)?
- Does running GEPA against an online optimisee change the **cost model** the loop must
  respect — per-token gateway cost + rate-limits + network variance replace the wall-clock /
  OOM ceiling that bounds local runs — and does the budgeter (`gepa_budget`) need an online
  cost/rate-limit dimension rather than pure wall-clock?
- With BOTH the reflector and the optimisee online, is there any leakage/contamination risk
  to guard (the same gateway model reflecting on its own traces)? Record it.

### Design decisions (resolved — plan-review 2026-06-28)

- **Optimisee model → configurable, BigPickle default.** Add an **online-optimisee mode**
  keyed off the existing `external_provider`+`model_ref` config fields (reuse
  `online-bigpickle.json` as the base/default; any online `model_ref` is accepted). The GEPA
  candidate's serve fields are **pinned** (`GEPA_REFLECTOR_FORBIDDEN_KEYS` already forbids the
  reflector from writing `external_provider`/`model_ref`/`base_url` — verified
  `harness_eval.py:1577`), so the optimisee model is fixed *per GEPA run* and only the TEXT
  levers evolve.
- **Mode selector → reuse `external_provider: true` (no new flag/field).** The mode is read
  off the *base/run config*, exactly as item 22 already drives the online serve path — when
  `external_provider` is on the run is online-optimisee, otherwise local. **Reconciles with
  "no silent mixing"**: mode is set by the base config, and the reflector is *forbidden* to
  change `external_provider` (frozen key), so the mode is unambiguous *per run*. A
  local-optimisee run (base `external_provider` false) whose merged candidate smuggles in
  `external_provider`/`model_ref`/`base_url` still **fails loudly** under the offline guard's
  existing logic (`harness_eval.py:1589-1595`). *(Rejected: a separate `--online-optimisee`
  flag / `optimisee_mode` field — redundant with `external_provider`, adds a second source of
  truth that could disagree with the serve path.)*
- **Guard split + WIRE BOTH INTO `cmd_run`.** Add `gepa_assert_online_optimisee` (asserts a
  pinned `external_provider`+`model_ref`, runs `online_preflight` — `harness_eval.py:306` —
  and asserts NO `mlx-local`/local-`baseURL` leak), and **call the mode-selected guard from
  `cmd_run` itself** (`harness_eval.py:2356`) so **every real candidate eval is checked**.
  ⚠ **Gap this fixes:** today `gepa_assert_serving_offline` is invoked **only in selftest**
  (lines 3061/3067/3222/3240) — `cmd_run` never calls it, so the 19/23 "asserted on every
  candidate" claim is currently enforced only by the manual driver, not by code. Wiring the
  guard into `cmd_run` **also retro-hardens the offline path** for items 19/23/25 (behaviour
  unchanged — offline base configs still serve local Gemma — but the invariant is now
  enforced, not assumed). Default (no `external_provider`) selects `gepa_assert_serving_offline`.
- **Fitness signal → shaped-T3 reward (`gepa_t3_shaped_score`, `harness_eval.py:1438`) on the
  6-instance T3 set** (tier-3 instances 21614/12481/21627/22714/18621/15346 — confirmed in
  `harness_eval_subset.json`); T2 micro stays the cheap rung. T1 (+T2 where measured) stay
  hard gates per item 23. **⚠ [lit-only] until 27.2:** the claim "BigPickle has real T3
  headroom → binary F2P flip is a live adopt gate, not a 0/8 wall" rests on item-22's **4/8**,
  which was the **mixed 8-instance** subset (T3+T4) at the 240s cap — **NOT** the 6-instance
  **tier-3-only** fitness set, on which BigPickle has **no recorded baseline**. 27.2 must
  measure it before the rung/ceiling choice is final.
- **Unlock ceiling → 1.0 for online mode (NOT the 0.50 behavioural cap).** ⚠ **Bug fixed:**
  `gepa_t3_gate_check` hard-codes `GEPA_T3_SHAPED_CEILING = 0.50` (`harness_eval.py:1434`) —
  the *behavioural* ceiling specific to the capability-bound 4B (best a text lever can do when
  the model can't land F2P flips). A capable optimisee reaches the **+1.0 F2P-flip rung**, so
  its shaped-T3 mean may already exceed 0.50 → the rule `(0.50 − mean) > spread` goes negative
  and would **wrongly gate a climbable signal**. **Online mode uses ceiling = 1.0** (the
  binary F2P-flip ceiling); **local runs keep 0.50 unchanged.** Parameterise the ceiling by
  mode rather than reusing the constant. `GEPA_T3_ADOPT_CEILING = 1.0` (the separate adopt
  gate) is unchanged for both modes.
- **Budget → add latency + rate-limit/network-variance now; token-cost recorded-but-zero.**
  Extend `gepa_budget` (`harness_eval.py:1675`, currently pure wall-clock
  `per_rollout_s × n × k`) with an **online dimension**: measured per-rollout **latency**,
  **rate-limit/retry count**, and **network-variance** handling (retry/backoff + abort on
  budget or persistent failure). **Token-cost is a recorded-but-zero field** for the free
  BigPickle default; a **real per-token gateway-cost ceiling is [lit-only]** until a *paid*
  `model_ref` is actually run on this machine.
- **Contamination → warn + record, do NOT hard-forbid.** Reflector (Opus-4.8) and the default
  optimisee (BigPickle, opencode zen gateway) are disjoint, so contamination risk for the
  default is low. If a user pins the optimisee to the same model family as the reflector,
  **emit a warning and record the reflector identity + the optimisee `model_ref` in the ledger
  `notes`** — but allow the run. *(Rejected: a hard optimisee==reflector ban — too rigid, and
  the disjoint default needs no guard.)*
- **Text levers only (unchanged).** The reflector still emits ONLY `system_prompt`/
  `rules_append`/`opencode_config`/`sampling`/`env` text — never the serve fields
  (`GEPA_REFLECTOR_TEXT_KEYS`, `harness_eval.py:1574`). Whether REPLACE-vs-APPEND suppression
  (item 18) reproduces on a *capable* model is itself a finding.

- [x] **27.1 Decouple the optimisee model + add the online guard, WIRED INTO `cmd_run`.** —
      **DONE (2026-06-28).** (a) `cmd_run` resolves the optimisee ref from `--model`→config
      `model_ref` and pins it back into `cfg` as the single source of truth; (b) added
      `gepa_assert_online_optimisee` (pinned `external_provider`+`model_ref`, `online_preflight`
      gated on `preflight=`, no `mlx-local`/local-`baseURL` leak) + the
      `gepa_assert_optimisee_mode` dispatcher; (c) **both branches of `cmd_run` now call the
      mode-selected guard on every candidate eval** — online when the base config sets
      `external_provider`, else `gepa_assert_serving_offline` (the online branch's bare
      `online_preflight` is now subsumed by the guard); (d) the offline guard is the default and
      now **enforced in `cmd_run`** (was selftest-only) → retro-hardens items 19/23/25,
      behaviour unchanged (all 19 shipped offline configs verified to pass the guard). **Also
      landed (design-decision code):** the **0.50→1.0 online unlock-ceiling fix** —
      `gepa_t3_gate_check(..., online=)` parameterises the ceiling by mode (`--online` flag on
      `gepa-t3-gate`), and `gepa_budget(..., online=)` adds the latency/retry/network-variance/
      token-cost(=0) sub-block. Selftest: 5 new item-27 checks (online guard accept + 4 rejects,
      mode dispatcher routing, the ceiling bug-fix, the online budget sub-block) — all green;
      `make check` clean across all 10 files.
- [x] **27.2 Online-GEPA feasibility gate (mirror 19.2) — MEASURE the missing baseline first.** —
      **DONE → GATED (near-ceiling, 2026-06-28).** K=3 baseline of BigPickle on the 6-instance
      tier-3 set (`online-bigpickle-t3-r1..r3`): **5/6, 5/6, 4/6 = 14/18 binary F2P flips**, shaped
      **mean 0.75** (runs 0.79/0.88/0.58), spread 0.292. Rungs: **14× +1.0** (real fix), 1× +0.25
      (tool-churn), **3× −0.25** (catastrophic — edit broke P2P). **This CLOSES the [lit-only]
      headroom claim:** BigPickle has real, large T3 capability — categorically unlike the local
      4B's 0/6 wall (item 22's 4/8 was the *mixed* T3+T4 subset; this is the tier-3-only fitness
      set). **The 0.50→1.0 ceiling fix was load-bearing:** mean 0.75 > the 0.50 local cap, so the
      old constant would have computed *negative* headroom and nonsensically gated. Applying the
      **online (ceiling 1.0)** unlock rule: headroom-to-1.0 (**0.25**) ≤ K-run spread (**0.292**)
      → **GATED**. ⚠ **Novel gate reason — the MIRROR of the local negative:** not "T3 wall holds /
      no capability" but **near-ceiling** — BigPickle is so close to a perfect 6/6 that the small
      remaining headroom is swamped by run-to-run noise (the spread is driven by the 3 occasional
      P2P-breaking edits, not by failures to engage). Budget (`gepa_budget` online dim, measured):
      per-rollout median **163.7s**, per-candidate **≈49 min** at K=3, fits 3 candidates in the 3h
      ceiling; retry/variance 0, token-cost $0 (free BigPickle). Gate report via
      `gepa-t3-gate --online --label-prefix online-bigpickle-t3-`.
- [x] **27.2b Build a harder online fitness surface (user-chosen path) → T4 UNLOCKED
      (2026-06-28).** Since BigPickle is near-ceiling on T3, switched the fitness surface to the
      **5 tier-4 (multi-file/reasoning) instances** (13043/15345/11400/18532/19007 — already
      prepared). Generalised the gate machinery to a **configurable tier**
      (`gepa_t3_shaped_stats(tier=)`, `gepa-t3-gate --tier`; selftest added). K=3 baseline
      (`online-bigpickle-t4-r1..r3`): **2/5, 2/5, 2/5** binary (mean 2.0/5, **spread 0** — temp 0.0
      is near-deterministic), shaped **mean 0.65, spread 0.0**, headroom-to-1.0 **0.35 > 0 →
      UNLOCKED**. Rungs: **6× +1.0** (15345/18532 solved), **6× +0.50** (11400 F2P 0/2, 19007 F2P
      1/3 — clean edit but fix wrong/incomplete), **3× +0.25** (13043 no-edit). Budget: per-rollout
      **60s**, per-candidate **15 min** at K=3, fits 12 candidates in 3h. **Reflection signal
      (deterministic): BigPickle's defect is VERIFICATION/COMPLETENESS, not engagement** — unlike
      the weak 4B, it edits on 4/5 but doesn't run the failing tests / cover all cases / always
      commit. This is the proper GEPA surface → 27.3 proceeds.
- [~] **27.3 Run GEPA with the online optimisee.** — **DESIGNED + STARTED; BLOCKED on a sustained
      free-gateway outage (2026-06-28).** Three candidates built + guard-validated against the T4
      surface, each varying ONLY `rules_append` (serve fields pinned to the online optimisee):
      (1) **`online-bp-t4-cand-verify`** — the reflection-targeted RICHER lever (run-failing-tests →
      iterate → cover-all-cases → always-commit), targeting BigPickle's verification/completeness
      defect; (2) **`online-bp-t4-cand2transfer`** — the local-4B winner (item-19 cand2 terse rules)
      verbatim, the "does terse transfer?" control; (3) **`online-bp-t4-naive`** — a content-free
      "be thorough" counter-arm. **⚠ Live blocker, NOT a result:** the free `opencode/big-pickle`
      zen gateway **rate-limited then went into a sustained outage** after ~20 back-to-back episodes
      (baseline 15 + cand-verify run-1). The online guard **correctly aborted** the throttled runs
      (preflight-fail, not opaque per-instance failures) — a live validation of 27.1's guard AND of
      the **network-variance/rate-limit budget dimension** (the free gateway's binding constraint is
      rate-limit, not wall-clock). cand-verify's partial rows (2/5, then 2× all-timeout) are
      CONTAMINATED by the outage → discarded; the clean re-run uses `-v2` labels. A robust paced
      runner (`scratchpad/run_27_3.sh`: wait-for-health → one candidate at a time → cooldown →
      retry) is **ready to resume** the moment the gateway recovers; polled ~40 min, gateway did not
      recover. **Resume:** re-run `run_27_3.sh` (or pin a different/paid online `model_ref` — the
      machinery is model-agnostic). Compare the winning text to local cand2; counter-arm keeps the
      negative honest.
- [ ] **27.4 Adopt only if it SURVIVES re-validation** — independent K≥3 re-run (reflector out
      of the eval path), the win within one spread of the online score, binary F2P flip + tool
      calls valid. Record whether the result transfers to / differs from the local-optimisee
      findings (the "is terse-wins a 4B artifact?" answer).
- [x] `make check` (ruff + mypy + pytest/selftest) green for any harness code touched;
      mode-selected guard (`gepa_assert_online_optimisee` online / `gepa_assert_serving_offline`
      local) asserted in `cmd_run` on every candidate. — **DONE (2026-06-28)** with 27.1 (ruff
      clean, mypy clean across 10 files, selftest OK incl. 5 new item-27 checks). *(Re-confirm on
      close after 27.2–27.4 add any code.)*

### Measurement plan (item 27)
- **Climbing signal:** the shaped-T3 mean (K≥3) of the **online optimisee** over the 6 tier-3
  instances (T2 micro available as the cheap rung); unlock rule `(ceiling − mean) > spread`
  with **ceiling = 1.0 for online mode** (0.50 stays the local-mode behavioural cap).
  **Adopt gate:** a binary F2P flip + tool-calls valid that **survives an independent re-val**.
- **The single lever varied:** the **text levers** (system/rules/tool text); the optimisee
  model is **fixed per run** (online, configurable; BigPickle default) and the topology is held.
- **Per-run metrics:** shaped mean + spread, binary F2P /6, made-edit / P2P-intact rate, plus
  the **online cost dimensions** — per-rollout latency, rate-limit/retry count, network-variance
  events (token-cost recorded-as-zero for the free default; real cost [lit-only] until a paid
  ref is run).
- **Cross-item comparison:** report whether the online-optimised winning text MATCHES or
  DIFFERS from item-19's local cand2 (the "is terse-wins a 4B artifact?" question).
- **Gate:** mode-selected guard enforced **in `cmd_run`** on every candidate
  (`gepa_assert_online_optimisee` online / `gepa_assert_serving_offline` local — the latter now
  also retro-hardens items 19/23/25); `make check` green for any code touched.

### Documentation (item 27)
- [x] **Update** `docs/structured-optimisation-research.md` — **DONE (2026-06-28, machinery
      part).** New §27: the `external_provider` mode selector, `gepa_assert_online_optimisee` +
      the `gepa_assert_optimisee_mode` dispatcher, the `cmd_run` guard wiring + offline
      retro-hardening, the 0.50→1.0 online unlock ceiling, the online budget dimension, and the
      27.2–27.4 pending runs. *(The "does the winning text transfer from the local 4B?" answer is
      filled in by 27.3/27.4.)*
- [x] **Update** `docs/opencode-local.md` — **DONE (2026-06-28).** New *GEPA against an ONLINE
      optimisee (item 27)* section: the online-GEPA mode, the explicit constraint-reframing
      (analysis-loop capability, never the frozen serve path), the mode-selected `cmd_run` guard,
      the 1.0 online ceiling, the budget dimension, and the commands.
- [ ] **Update** `CHANGELOG.md` only when item 27 closes.

### 28. Formal-verifier stage in a PLAN → VERIFY → IMPLEMENT loop  ← deep-research + local-eval item

**Goal.** Test whether inserting a **verifier stage** — a formal-methods-derived,
automatic, cheap PASS/FAIL signal — **between planning and implementation** improves the
weak local 4B's real-fix rate. Items 16/18/19/23 proved the harness is sound and the 0/8 is
**capability-bound**; item 20 found the only real T3 fix (22714) came from the multi-agent
topology but was **OOM/variance-bound, not robust**, and that the weak model **churns
grep/read** and **edits without checking its own work**. This item asks: if a planner drafts
a goal, a **verifier** mechanically gates the plan/edit against a spec-free correctness signal
(does it parse / type-check / not break the regression tests / satisfy a generated property)
**before** the implementer commits, does the weak model land more fixes and waste fewer
rollouts? The architecture is a three-role loop — **planner → verifier → implementer** (an
extension of item-20 arm-c's orchestrator + planner/coder subagents, with a verifier role
added) — and is the **longer, multi-agent shape**, so it runs at a **900 s per-instance cap**
(vs the 600 s Gemma default), set via the config `timeout` field (already supported,
`harness_eval.py:2519`; `online-bigpickle.json` uses it).

> **Hard constraints carry through (items 8–11).** Fully **local / offline at serve time**,
> **16 GB M1**, model + serving engine **FROZEN** (Gemma-4-E4B QAT, mlx-lm 0.31.3). The
> verifier must run **locally and offline** and integrate as an **opencode tool** (a
> `.opencode/tools/*.ts` shadow tool, the item-21 code-mode sandbox, or a local MCP server)
> so it rides the existing harness lever path. A verifier needing the cloud, a non-Python
> translation step, or a hand-written formal spec per instance is out of scope. Tool-call
> reliability stays a hard floor (repair proxy ON).

> **⚠ Anti-leakage constraint (decisive — the benchmark validity floor).** The hidden
> **fail-to-pass (F2P) tests are the GROUND-TRUTH label** and must **never** be visible to
> the in-loop verifier — handing them to the agent is benchmark cheating, not a fix. The
> verifier may consume ONLY spec-free signals the agent could legitimately compute at solve
> time: syntax/parse, static type-consistency, lint, the **pass-to-pass (P2P) regression
> subset** ("don't break what already works"), and **agent/LLM-generated** properties or
> asserts (which are themselves fallible — see the spec problem below). 28.2 must assert F2P
> isolation in code, and 28.3 must show any gain is NOT an F2P leak.

> **The specification problem (the central risk).** Real bug-fix tasks ship **no formal
> spec**, so heavyweight deductive verifiers and proof assistants that *require* one are
> structurally a poor fit (see the framework assessment below). The verifier signal must be
> **derivable without a per-instance hand-written spec**. Where a spec is synthesised
> (LLM-as-spec-writer → Hypothesis properties / `deal`/`icontract` contracts), its
> **reliability is itself an open question** — a wrong generated property can mislead the weak
> model worse than no verifier (cf. item 18: bad guidance suppresses tool use). Treat
> generated-spec arms as hypotheses, not assumed wins.

> **Evidence policy.** Every "verifier X helps" claim — and the framework recommendation
> below — is **[lit-only]** until a **local K≥3 A/B** on the shaped-T3 set closes it. The 28.1
> survey *ranks*; only a harness run *adopts*. Per the counter-arm rule, a "verifier doesn't
> help here" outcome is built and measured, never assumed from papers.

### Framework assessment ([lit-only] — 28.1 deep-research DONE 2026-06-28, `docs/formal-verifier-research.md`)

> ✅ The 28.1 deep-research survey **ran 2026-06-28** (`wf_29a63d03-7d2`: 27 sources, 25 claims
> adversarially verified, 24 confirmed) and **confirms the domain-reasoned ranking below with
> three refinements** (full cited write-up: `docs/formal-verifier-research.md`):
> 1. **CrossHair (Z3-backed concolic) is the best formal-methods-derived fit for untyped Python**
>    — runs the live function with symbolic-proxy objects, no static types needed → promote it to
>    Tier-1-adjacent (still needs a property/contract to check, so a strong model authors it).
> 2. **pyright > mypy** as the type gate — pyright type-checks ALL code + infers return types;
>    mypy skips unannotated functions. Prefer pyright.
> 3. **The test signal is WEAK and GAMEABLE** — 20–31% of "solved" SWE-bench patches are
>    semantically wrong; frontier models reward-hack tests 76–93%. Hardening the verifier +
>    withholding hidden F2P is **load-bearing**, not optional (sharpens the anti-leakage rule).
> The capability-tier gap is the headline caveat: **essentially NO published evidence a verifier
> helps a weak ~4B local model on repo bug-fixing** → item 28's 28.3 A/B is the open empirical
> contribution. The constraints below stand: **(A) spec availability** (no per-instance formal
> spec exists) and **(B) local runtime cost** (16 GB M1, cheap dense pass/fail).

- **TIER 1 — ADOPT-candidate verifier signals (spec-free, local, cheap, dense):**
  - **The repo's own runnable tests — P2P regression subset ONLY** (never F2P). The single
    strongest spec-free oracle; zero spec-writing; already computed by the harness scorer. In
    the loop it answers "did the edit break working behaviour?". *Cost:* one test run/iteration.
  - **Static type-consistency — `mypy` / `pyright`.** No spec needed, fast, fully local,
    catches a real error class (bad attributes, arg/return mismatches) the 4B introduces.
    Dense-enough gate signal.
  - **Lint / parse — `ruff` / `pyflakes` / `ast.parse`.** The cheapest filter (sub-second):
    does the edit parse, no undefined names, no broken imports. The natural first gate before
    any heavier check.
- **TIER 2 — PROTOTYPE (higher value, real reliability risk):**
  - **Property-based testing — `Hypothesis`**, with **LLM-as-spec-writer** generating the
    properties. Spec-free *infrastructure* but the *properties* are model-generated and
    fallible (the spec problem). Worth one arm; gate on whether generated properties are sound.
  - **`CrossHair`** (concolic / **Z3**-backed symbolic execution of Python). Finds
    counterexamples to contracts/asserts without a full suite — but needs `deal`/`icontract`
    contracts or inline asserts to check against, and is **slow on non-trivial code** (the 16 GB
    /latency budget bites). Niche; behind Hypothesis.
- **TIER 3 — AVOID for this stack (constraint (A) and/or (B) fails):**
  - **Deductive verifiers / proof assistants — Dafny, Frama-C, Verus, Why3, Coq/Rocq, Lean 4,
    Isabelle, Nagini/Viper.** All require a **hand-written formal specification** and/or a
    **non-Python translation/autoformalization** step that does not exist for arbitrary
    SWE-bench Python. The autoformalization cost dwarfs any benefit on a 16 GB M1, and the
    spec problem (no per-instance spec) makes them structurally inapplicable here.
  - **Raw SMT (`Z3`/`CVC5`) as a direct verifier** — only applies once a logical model is
    extracted, which is the hard part for arbitrary repo logic. (Used *indirectly* under
    CrossHair, not directly.)
  - **Bounded model checkers — `CBMC` (C-only), `ESBMC`** (ESBMC-Python frontend exists but is
    immature for real repos). Avoid for the first pass.
  - **LLM + theorem-prover work (DeepSeek-Prover, AlphaProof, LeanDojo, Lean Copilot, Baldur,
    Thor)** — demonstrated on **competition math / Lean-formal-spec** settings, **does not
    transfer** to spec-free practical Python bug-fixing. Out of scope.

> **Recommendation (preliminary, prove via 28.1 + 28.3):** wire the verifier stage from the
> **Tier-1 spec-free signals — P2P-regression-run + `ruff`/parse + `mypy` type-check** — as the
> cheap, dense, no-spec gate the 4B loop can actually consume, then (only if Tier 1 moves the
> signal) prototype **one Tier-2 LLM-generated-`Hypothesis`-property arm** to test whether a
> synthesised spec adds anything net of its reliability risk. **Do not** invest in deductive
> verifiers / proof assistants: they fail the no-spec constraint outright on this workload.

### Feasibility probe — can the 4B write/use Lean? (MEASURED 2026-06-28 — `docs/item28-lean-probe-notes.md`)

A cheap build-time probe (Lean 4.31.0 via elan; the live local 4B on `:8080`; greedy)
testing the user-raised idea "let **Opus author the Lean spec** (the hard part); test whether
the 4B can write/use Lean". Two regimes gated:

- **Regime B (4B WRITES Lean): NOT viable — 3/6 sorry-free compiles, and the gap is the
  proofs.** The 4B writes trivial `def`s (A1 2/2) but **cannot reliably write proofs**: the
  *easier* goal `n+0=n` (closes by `rfl`) FAILED both conditions by **over-engineering** — the
  correct one-liner compiles in bare Lean (control exit 0), so it is the model's failure, not
  the environment's; the harder `0+n=n` passed only when instructed (`by simp`). The
  **planner-Lean-instructions** hypothesis helps (flipped A3 to PASS, suppressed hallucinated
  `import Mathlib…`) but did **not** close the proof gap. *Cost confound (a finding itself):* a
  real Lean-proof env needs **Mathlib = multi-GB + long build on the 16 GB M1** → fails the
  local-cost constraint. Since the proof *is* the verification, Lean-as-4B-output is out →
  **confirms the Tier-3 "avoid" placement empirically, not just by argument.**
- **Regime C (Opus authors the spec, 4B READS it, fixes in Python): mechanically feasible but
  UNPROVEN to help — 4/4 fixes, non-discriminating.** The 4B consumes a Lean spec without being
  derailed (so Opus-writes-the-spec *does* remove the spec-availability blocker), but the Lean
  spec did **not beat a plain-NL spec** and was **slower** (e.g. 38.6 vs 21.9 s) — the probe
  tasks were too easy to discriminate. ⇒ if pursued, Lean is only ever a **regime-C planning
  artifact** (never compiled against the candidate Python — that reintroduces the Python↔Lean
  autoformalization gap), added as a **speculative arm that must beat an NL spec** on a
  *discriminating* task set, ranked **below** the Tier-1 gate.

> **Net:** the probe does **not** change the headline recommendation (Tier-1 spec-free signals).
> "Opus writes the spec" is sound and removes blocker #1, but (a) the 4B can't write the proofs
> regime B needs, and (b) on the read side a Lean spec wasn't worth its latency vs NL on easy
> tasks. Lean stays **Tier-3 for the verifier role**; the only live Lean question is the
> regime-C plan-spec arm, deferred behind Tier-1 and gated on beating NL.

> **⚖ RESOLVED DECISION (user, 2026-06-28) — capability-tiered: for LOCAL models < 16 GB,
> AVOID formal proving (Lean/Coq/proof-assistants) for now.** The probe shows the proof-writing
> capability isn't there at the 4B/16 GB tier (3/6, zero reliable proofs), so formal proving is
> **out of scope for the local arms** of item 28 — they use the **Tier-1 spec-free verifier**
> only. Formal proving is **NOT abandoned in general**: it is re-opened as a **separate
> capability-gated question for capable ONLINE models** (BigPickle-class). Mirrors item 27's split
> (frozen-local vs online optimisee): the local serve path stays proof-free; the online branch
> is where the proof-assistant architecture is allowed to be tested.
> **⚠ But the equivalent online probe (MEASURED 2026-06-28, `docs/item28-lean-probe-notes.md`)
> already shows capability is necessary-NOT-sufficient:** BigPickle wrote a valid compiling
> inductive proof of `n+0=n` in 4.5 s — the exact task the 4B failed both ways, so it clears the
> proof-writing bar — **but 28.1's cited research is decisive that clearing that bar does NOT make
> Lean a viable verifier for Python** (the spec-availability + Python↔Lean autoformalization walls
> are capability-independent; AlphaProof/DeepSeek-Prover only work where a formal statement
> pre-exists). ⇒ even on the online branch the recommended verifier is an **AutoCodeSherpa-style
> PBT + symbolic-condition gate, NOT Lean**. (Fuller BigPickle run blocked by gateway throttling;
> script is the deliverable, re-runnable.)

**Open questions this item must settle:**
- Does a verifier *gate between plan and implement* raise the **shaped-T3 mean** past spread
  (survives re-val) on this stack, or does the extra stage just add latency/OOM exposure (the
  900 s cap is a symptom — does the longer loop pay for itself)?
- Which Tier-1 signal carries the gain — regression tests, type-check, or lint — and do they
  **stack** or is one sufficient? (Ablate.)
- Does the verifier **reduce wasted rollouts** (fewer no-edit/edit-then-break, lower OOM rate)
  even when it doesn't flip a fix — i.e. is it a *reliability* win like item-21 code-mode, not a
  correctness win?
- Does an **LLM-generated spec** (Tier 2) help or *mislead* the weak model (the item-18 "bad
  guidance suppresses" risk applied to specs)?

### Design decisions (to be resolved — plan-review pending)

Open for the plan-review pass (do not assume answers — but these are the leaning defaults):

- **Verifier signal axis** — Tier-1 spec-free (P2P-regression + type + lint) vs a Tier-2
  generated-spec arm. *Leaning:* Tier-1 first (28.2), Tier-2 only if Tier-1 moves the signal.
- **Topology** — extend item-20 arm-c (`task` tool + `planner`/`coder` subagents) with a third
  **`verifier`** subagent (read-only + run-checks tool), the orchestrator looping
  plan→verify→implement→re-verify. *Tension (record, don't hide):* item 20 found the `task`
  tool **never fired** on the weak 4B — so a single-pass `rules_append` procedural approximation
  (plan, then run the checks tool, then edit) is the fallback if the multi-agent mechanism stays
  inert. Both forms ride text/topology levers only (`gepa_assert_serving_offline` passes).
- **Verifier mechanism** — a local `.opencode/tools/*.ts` shadow tool (or the item-21 code-mode
  sandbox) that runs ruff/parse + mypy + the P2P subset and returns a structured pass/fail the
  agent reads. **F2P tests are withheld from this tool by construction** (anti-leakage).
- **Timeout → 900 s per instance for the multi-agent verify arms** (vs 600 s Gemma default),
  set via the config `timeout` field (no code change — already honoured at
  `harness_eval.py:2519`). Baseline/bare arms stay at their existing caps so the delta is the
  topology, not the clock. *(Resolved per user request 2026-06-28.)*
- **Scoring regime** — reuse the item-23 shaped-T3 reward on the 6-instance set
  (`harness_eval_subset.json`) + the item-20 churn metrics (tool_call_rounds, output tokens,
  OOM rate, made-edit/P2P-intact), so outcomes are comparable across items 20/23/25/26.
- **Budget / abort** — the verify loop is longer (extra checks + re-verify) → size from the
  ~257 s T3 median × the added verify passes via `gepa_budget`; go/no-go before the expensive
  multi-agent arm, abort → fallback to the single-pass procedural verify arm.

- [x] **28.1 Deep-research survey + framework evaluation — DONE 2026-06-28** (`wf_29a63d03-7d2`:
      27 sources, 25 claims adversarially verified → 24 confirmed / 1 killed). Surveyed
      verifier-guided code-gen (LEVER, CodeT, AlphaCodium, self-repair/Olausson), the
      formal-verification frameworks, LLM+theorem-prover transfer (AlphaProof/DeepSeek-Prover —
      **Lean/Coq NOT viable for either tier; capability alone does NOT unlock them without a
      formal spec**), the spec problem (LLM-as-spec-writer; AutoCodeSherpa PBT+symbolic gate is
      the best SWE-bench-demonstrated pattern, but on a capable online model), and plan→verify→
      implement multi-agent work. Confirmed the reasoned ranking + 3 refinements (CrossHair up,
      pyright>mypy, test-signal-gameable). Output: **`docs/formal-verifier-research.md`** (cited,
      [lit-only]). Key caveat recorded: **no published evidence a verifier helps a 4B local model
      on repo bug-fixing → 28.3 is the open empirical contribution.**
- [ ] **28.2 Build the verifier tool + arm configs (NO scored run).** A local, offline
      verifier tool (ruff/parse + mypy + P2P-regression subset; **F2P withheld, asserted in
      code**) wired as an opencode tool / code-mode call. Arm configs under
      `scripts/harness_configs/` on the cand2 base: (a) single-pass procedural verify via
      `rules_append`; (b) multi-agent planner→verifier→implementer (extends arm-c) with
      **`"timeout": 900`**; plus a Tier-2 generated-`Hypothesis`-property arm if 28.1 supports
      it. `gepa_assert_serving_offline` passes on each. Build-time feasibility smoke (does Gemma
      call the verifier tool + read its result + does it fit memory) — a failed smoke is a
      recorded wall-confirming null, not an abort. `make check` green + selftest for the new
      config-load / F2P-isolation / 900 s-timeout-honoured logic.
- [ ] **28.3 Local A/B on the shaped-T3 set, K≥3 — the actual evidence.** Score the verify arms
      vs bare + cand2 + (where relevant) item-20 arm-c, with `gepa_t3_shaped_score` + the churn
      metrics, at the 900 s cap for the multi-agent arms. **Adopt** iff a verify arm lifts the
      shaped mean past spread **and survives an independent re-val** (item-20 discipline) **OR**
      materially cuts wasted rollouts/OOM without regressing fixes — **and** the gain is shown
      **not** to be an F2P leak. Valid outcomes (all closed, per Evidence policy): (i) adopt the
      verify topology; (ii) partial — verifier improves reliability/churn but not fix-rate;
      (iii) negative — a verifier stage does not help the weak 4B here (the [lit-only] verdict
      locally falsified; ties to item-20's "the wall is OOM/capability, not missing checks").
- [ ] `make check` (ruff + mypy + pytest/selftest) green for any harness/tool code touched;
      `gepa_assert_serving_offline` asserted on every arm config; F2P-isolation asserted.

### Measurement plan (item 28)
- **Primary:** item-23 shaped-T3 mean (K≥3) over the 6 T3 instances, unlock rule at the 0.50
  behavioural ceiling; **adopt gate** = binary F2P flip /6 (full pass/8 held) that survives
  re-val. **Secondary (the reliability thesis):** tool_call_rounds, output tokens, OOM rate,
  made-edit / P2P-intact, steps-to-first-edit — does the verifier cut wasted work?
- **The single lever varied:** the **verification stage** (none → spec-free Tier-1 gate →
  generated-spec Tier-2). Rules text held at the cand2 base; baselines = bare + cand2 + arm-c.
- **Timeout:** **900 s** per instance for the multi-agent verify arms (config `timeout` field);
  600 s for the non-verify reference arms. K≥3 mean + spread per arm.
- **Gate:** local/offline + 16 GB-fit asserted; `gepa_assert_serving_offline`; **F2P withheld
  from the verifier, asserted in code**; `make check` green.

### Documentation (item 28)
- [x] **Added** `docs/item28-lean-probe-notes.md` — the 2026-06-28 Lean feasibility probe
      (regime B 4B-writes-Lean 3/6, regime C 4B-uses-Opus-spec 4/4 non-discriminating; the
      proof-gap + Mathlib-cost findings that keep Lean Tier-3 for the verifier role).
- [ ] **Add** `docs/formal-verifier-research.md` — the 28.1 survey (ranked, feasibility-screened,
      cited, [lit-only]) + the refreshed framework recommendation.
- [ ] **Update** `docs/tiered-harness.md` — note item 28 reuses the shaped-T3 + churn metrics and
      the anti-F2P-leakage rule.
- [ ] **Update** `docs/opencode-local.md` + `CHANGELOG.md` only when item 28 closes.

---

## Notes / open questions

- **Sequencing.** 16 → (18, 19, 20) → (25, 26, 27, 28). Item 16 is the prerequisite: a mechanically
  broken full harness can't give signal for 19's optimiser or 20's planning A/B. Item 20's
  (ii)-partial close spawned **25** (GEPA-optimise the multi-agent planner prompt — combines
  item 19's GEPA loop with item 20's arm-c topology) and **26** (codegraph-class exploration
  tools to cut the explore-churn / OOM wall that item 20 surfaced); 25 reuses 19+23 machinery,
  26 can stack with 25. **27** (online-optimisee GEPA) generalises 19's loop off the frozen
  local Gemma using item-22's `external_provider` path — BigPickle-as-example, configurable —
  to test whether GEPA's text-lever findings transfer to a capable model. **28** (formal-verifier
  stage) also follows item 20: it adds a plan→**verify**→implement loop (extends arm-c's topology
  with a verifier role) and reuses the item-23 shaped-T3 reward + item-20 churn metrics; runs at a
  900 s cap as the longer multi-agent shape. 26 and 28 are complementary (26 = better exploration
  *into* the plan; 28 = a correctness gate *out of* the plan before the edit commits).
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
