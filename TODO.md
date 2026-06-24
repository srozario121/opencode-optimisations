# TODO — opencode-optimisations

The repo's running work-ledger. Item **16** is the open, diagnosed bottleneck
(carried over from the original ledger); items 17+ are the new work from the
2026-06-22 planning session. **Completed items 1–15 now live in `CHANGELOG.md`.**

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

### 16. Full-harness trace-driven fixes (round 2)  ▲▲ PREREQUISITE / dominant bottleneck

**The core finding that reframes everything below.** Item 14's micro-suite win
(0.62→1.00) **did not transfer** — the full harness still scored **0/8 on
SWE-bench**. A review of **16 episode traces** found the bottleneck is **not prompt
phrasing** but concrete harness/tool defects, **dominated by degenerate decoding
loops** (the model repeats the same planning sentence 15–25× before any tool call,
burning the whole episode budget). 7 failure modes were catalogued with instance
IDs as evidence.

> **Implication for items 17–19:** prompt/skill optimisation (incl. GEPA) is
> **premature** until the full harness produces a non-zero, non-degenerate signal.
> Land the mechanical fixes below **first**.

**Enabling changes (re-implemented FRESH here — DONE 2026-06-23):**
- [x] **E1 — Reduce default instance timeout 30 → 10 min** (`DEFAULT_INSTANCE_TIMEOUT`
      in `scripts/harness_eval.py`; long episodes are degenerate, not productive). Done.
- [x] **E2 — Real-time progress heartbeat** per episode. Done — `run_opencode_episode`
      now `Popen`-streams opencode's `--print-logs` stderr, parsing `message=loop step=N`
      to emit `· <iid> +Ns step=K` lines live (plus a 30s "(working…)" tick).
      Verified live: a real episode streamed step=0…5 in real time. This is also the
      timeout-enforcement path (deadline kill) and the E0 fallback metric source.

**E0 — Episode-metrics instrumentation (prerequisite — the adopt/reject signal): DONE 2026-06-23.**
- [x] Emits per-episode signals to the JSONL ledger + renders a "degenerate-loop
      gradient" table (`_render_episode_metrics`): **degenerate-loop rate** (repeated-line
      detection on assistant text), **frac-of-budget-to-first-tool-call**,
      **steps-to-first-edit**, **made-edit rate**, **tool-call + tool-call-error counts**,
      output-tokens, steps, timeout rate. Parser `parse_episode_jsonl` + 5 selftest
      checks; **validated against a real captured episode AND end-to-end live**
      (5 steps / 3 tools / edit@step4 / frac→tool 0.20).
- **Source (verified live 2026-06-23):** `opencode run --format json` → NDJSON,
      one event/line `{type,timestamp,sessionID,part}`; types `step_start` /
      `tool_use` (`part.tool`, `part.callID`, `part.state.{status,input,time}`) /
      `step_finish` (`part.tokens.output`, `part.reason`) / `text` (`part.text`).
      ⚠ **Caveat that shaped the design:** `--format json` **buffers to EOF**, so a
      timed-out (degenerate) episode — the case we most want to measure — leaves an
      **empty** jsonl. Hence the hybrid: json for completed episodes, streamed-stderr
      fallback (`steps` + `timeout ∧ ¬exiting-loop ⇒ degenerate`) for timeouts.
      *(`--print-logs` stderr carries NO assistant text at any log level — only loop
      steps — so per-token/per-tool detail must come from the json stream.)*
      Still TODO: fold this schema into `docs/opencode-local.md` (currently in code +
      this ledger).

**E-sampling — Sampling-plumbing audit (prerequisite for L1; also audits L2):**
- [x] **CORRECTION (2026-06-23): the original claim was wrong for this repo.** The
      `sampling` block **IS** consumed — `apply_levers` (`harness_eval.py:351-353`)
      copies it verbatim into the served model's `options`, and `cmd_run` records it
      in the ledger. So L2 low-temp *did* have a path. Added `selftest
      --check-sampling` to lock the forwarding in (asserts the block, incl.
      `repetition_penalty`, lands in the written `opencode.json`).
- [x] **mlx-lm 0.31.3 endpoint honours anti-repetition (source-verified, offline).**
      `mlx_lm/server.py` reads `repetition_penalty` + `repetition_context_size` (and
      `presence_penalty` / `frequency_penalty`) from the request body and feeds them
      into `LogitsProcessorArguments` → the sampler (lines 1180-1181, 1390-1398).
      ⚠ **`no_repeat_ngram_size` is NOT supported** — silently dropped. **→ L1 must
      use `repetition_penalty`, not `no_repeat_ngram_size`.**
- [ ] **[needs-live-verification]** One link is still unverified offline: whether
      opencode's `@ai-sdk/openai-compatible` provider serialises a **non-OpenAI**
      `options` key (`repetition_penalty`) into the actual wire request body, or
      drops/relocates it. **Confirm via the repair-proxy request log on the first
      L0/L1 run** before trusting an L1 number.

**Levers — status 2026-06-23 (L0 gradient → L6 diagnosis → full-subset confirm).**
The original ranking (L1 anti-repetition = top) assumed degenerate decode loops
dominate. **L0 measured that wrong:** degen 0/8; dominant mode = **dropped-output
3/8**. Investigated it (L6) → the Gemma-4 template forces thinking when tools are
present, and `enable_thinking=False` *looked* like a fix on n=4 — but the **full
8-instance confirm did not replicate it** (dropped 38%→38%). **Net: no lever
adopted yet.**

> **⚑ METHODOLOGY FINDING (changes how every lever is judged).** The L6 n=4→n=8
> reversal proves **single-run deltas are unreliable** on this stack — the *same*
> instance + *same* config flips dropped↔acts across runs. **Investigated the source
> (2026-06-23) and the fix is NOT a seed:**
> - The **tool-call generation path is non-deterministic even at `temperature=0`
>   with a fixed `seed`** — replaying one captured request 3× each way gave 3
>   *different* tool calls every time (raw mlx:8081, no proxy). The thinking-ON path
>   *was* stable, but the no-think/tool-call path is not.
> - This is **MLX/Metal floating-point kernel nondeterminism** (GPU reduction order),
>   *below* the sampling layer — so neither `seed` nor `temperature` can remove it,
>   and the serving stack is **frozen**.
> - **⇒ The ONLY reliable measurement is K-run averaging.** Adopt/reject requires
>   **K≥3 runs/config** with a delta that clears the run-to-run spread; there is no
>   determinism knob on this stack. A single 8-task pass cannot close a lever.
> - Constrains item 17 (fitness fn MUST average K repeats) and item 19 (GEPA eval
>   budget ×K). `MLX_PROXY_SEED` was wired (and is honored) but **does not achieve
>   reproducibility here** — kept only for hygiene / any future temp>0 use.

Current order: **(methodology fix first) → L3 (edit, most deterministic) > L5
(loop) > L1/L2/L4; L6 parked (inconclusive, toggle retained).**

- [x] **L6 — Disable executor "thinking" on tool turns — INVESTIGATED, INCONCLUSIVE
      (not adopted).** **The dropped-output root cause was NOT streaming repair (that
      hypothesis was wrong).** Diagnostic capture
      (`MLX_PROXY_CAPTURE`, built 2026-06-23) + replaying the exact failing request
      showed: the **Gemma-4 chat template forces a thinking phase whenever `tools`
      are present**, and the weak E4B executor spends the turn on `reasoning` and
      emits EOS **without a tool call** (captured turn: 509 reasoning chars, 0 tool
      calls, `finish=stop`). Replaying with `enable_thinking=False` flipped
      `finish_reason`→`tool_calls`. **Confirms item 20's "thinking hurts thin
      executors" on this stack.**
      - Fix: `mlx_repair_proxy.py` gained **`MLX_PROXY_NO_THINK=1`** (off by default,
        forwarded by `mlx.sh`) — injects `chat_template_kwargs={"enable_thinking":
        false}` into tool requests. Request-shaping, not an engine change.
      - **4-instance A/B looked decisive** (dropped-output 3/3→0/3, rounds 0→14/0→6/
        0→364) — **but it did NOT replicate.**
      - **⚠ FULL 8-instance confirm (2026-06-23): NO_THINK NOT adopted — the n=4 win
        was sampling noise.** dropped-output **38%→38%** (the SAME 3 instances —
        12481/11400/19007 — dropped again at 0 rounds), pass **0→0**. The capture
        proves NO_THINK *was* active (13/14 tool responses had `reasoning=0` vs
        baseline's 509-char reasoning), so thinking was genuinely suppressed — yet the
        headline metric didn't move. The same instances that *acted* under NO_THINK at
        n=4 *dropped* at n=8 → **dropped-output is a high-variance / stochastic mode**
        (no seed, mlx default temp), not a deterministic consequence of thinking.
        Secondary deltas were noise-level/mixed (mean_rounds 6.4→4.6, timeout 1→0,
        no-edit 4→5). **Verdict: INCONCLUSIVE — do NOT default `NO_THINK=1`.** Lever
        kept available (toggle stays), but unproven on this stack.
      - **Hints (n=4 only — NOT replicated, treat as leads not findings):** under
        NO_THINK, 12481 once *made an edit that produced no net diff* (→ a lead for
        **L3 edit-application**) and 19007 once hit **364 tool-call rounds** (→ a lead
        for **L5 loop control**). Both instances *dropped* (0 rounds) in the full run,
        so these are unconfirmed — verify under the K-run methodology before acting.
      - [x] **Capture instrumentation** (`MLX_PROXY_CAPTURE=<dir>`, off by default;
        dumps `.req.json` + `.resp.json`/`.resp.sse`; never alters served bytes) —
        reused by item 18 (trace ingestion).
      - [x] **Full 8-instance confirm DONE** → did not replicate (see ⚠ above).
      - [ ] If revisited: test NO_THINK under a fixed seed / K-runs (it *did*
        suppress thinking — captures show `reasoning=0` — just didn't move the metric
        in one pass). Still carries the pending L1 `repetition_penalty` wire check.
- **L3 — Edit application — DIAGNOSED 2026-06-23 (reproduced from artifacts).** Two
      distinct real defects, not the single "whitespace match" originally assumed:
  - [x] **L3a — patch-capture bug (FIXED).** sympy-12481 (NO_THINK) made a correct
        edit then ran `git add` + `git commit`. `capture_model_patch` diffed the index
        **vs HEAD** (`git diff --cached`), so a committed change showed **nothing** →
        mis-scored `no-edit` and **never tested** (score_instance short-circuits on an
        empty patch). Fixed: diff the index **vs `base_commit`** (captures the change
        whether committed or left in the worktree; the docstring already claimed this).
        Verified by git-semantics test + a new selftest check. Deterministic bug fix —
        **adopted** (exempt from K-runs).
  - [x] **L3b — forgiving edit matcher BUILT (`.opencode/tools/edit.ts`).** Shadows
        the built-in `edit` (filename precedence, like read/grep). Cascade, each step
        only on the previous miss so already-matching edits are UNCHANGED: (a) exact;
        (b) strip the read line-number gutter (`\d+ \| `, from `read.ts:110`) off
        oldString/newString then exact — fixes the 15345 case; (c) whitespace-flexible
        unique-window match (trim-per-line) with newString re-indented to the file —
        fixes the 13043 case. Refuses ambiguous (>1) / zero matches. **Validated:**
        6/6 unit cases incl. both real failures + exact-control + ambiguous/replaceAll
        (ran the real `execute()` with a stubbed `tool` wrapper); **live smoke** —
        opencode loads it (no edit.ts error) and a real edit applied end-to-end.
        Primary metric: **edit-apply success ↑** (deterministic).
      - [x] **Measured (K=3, 2026-06-23): correct fixes, but NOT exercised this run →
        no aggregate effect; pass still 0/8.** vs L0: dropped 38%→**38%** (spread
        0.38–0.38), made_edit 25%→29% (0.12–0.50), pass 0→**0 (spread 0–0)**.
        Crucially **0 git-commit episodes, 0 `edit:error`s (4 in L0), 0 forgiving-path
        hits** — the defects L3a/L3b fix (committed edits, gutter/whitespace edit
        errors) are **intermittent and didn't recur** in these 3 repeats (the model
        produced different, exactly-matching edits). So L3a/L3b are verified-correct
        *insurance* that activates when the defect reappears, not a measurable mover
        on this sample. (L1 wire check NOT addressed — baseline config has no
        `repetition_penalty` to forward; needs a config that sets it.)
- [ ] **L2 — grep fixed-string fallback** (`rg -F`) in `.opencode/tools/grep.ts`.
      **Low — no grep-parse failures observed in L0;** revisit if the metric appears.
- [ ] **L4 — Post-edit syntax check** feedback (same shadow/hook seam as L3).
      Secondary — only bites once edits land. Primary: **broken-syntax rate ↓**.
- [ ] **L5 — doom_loop policy** (native `doom_loop` vs proxy loop-detector).
      **Promoted (post-L6): 19007 under NO_THINK hit 364 tool-call rounds → timeout**
      — the genuine degenerate tool-call loop, which only surfaces once thinking is
      off. Pairs with L6. Primary: **stuck-loop count / max-rounds ↓**.
- [ ] **L1 — Anti-repetition sampling** (`repetition_penalty`, NOT
      `no_repeat_ngram_size` — mlx-lm drops the latter; see E-sampling).
      **DEMOTED — degenerate-loop rate was 0/8 in L0**, so its target mode didn't
      occur. Keep as a cheap config-only experiment but do not lead with it. Still
      the run that confirms the `repetition_penalty` wire [needs-live-verification].
- [x] **L0 — Re-baseline** at the 10-min timeout. **DONE 2026-06-23: 0/8** — see
      the "⚑ L0 baseline result" subsection below for the full E0 gradient that
      drove this reprioritization.

### ⚑ Where item 16 stands (2026-06-23, after L0 + L6 + L3 + K-run measurement)

**The harness floor is now solid; pass-rate is still 0/8 and the binding constraint
has shifted off the harness.** Enablers (E0/E1/E2/E-sampling) + K-run support done.
Levers attempted: **L6** (no-think) inconclusive (noise); **L3a/L3b** correct but
their target defects didn't recur in the K=3 run (no aggregate effect). Across every
run the dominant modes are: **dropped/no-edit ~38–50%** (stochastic thinking-stop —
L6 territory, unfixed) and **tests-failed** (the model edits but the fix is *wrong*).
The latter is **model capability, not a harness defect** — no remaining lever (L2 grep
/ L4 syntax / L5 loop) targets "wrong fix" or the stochastic drop.

**Implication / likely pivot:** harness levers appear largely **exhausted for moving
0→>0 on THIS subset** — 8 SWE-bench-Lite sympy bugs may simply be beyond Gemma-4-E4B
regardless of harness polish. The highest-value next move is **item 17 (tiered
validation harness)**: add **T1-class easy tasks** (single-file, one-edit, no-search)
so a weak model can register a *non-zero* signal at all — without which no lever
(or GEPA, item 19) has a gradient to optimise. Recommend pausing further L-levers and
standing up the tiered set. *(RESOLVED 2026-06-23 — item 17 landed: the tiered
ladder + per-tier × failure-mode report now provide that gradient; T1/T2 are the
weak-model-passable rungs. A fresh `run` under each lever is the next step.)*

### ⚑ L0 baseline result — first full run on THIS machine (2026-06-23)

Ran `baseline` over the frozen 8-instance sympy subset at the new 10-min cap.
**0/8 pass** (confirms the premise). But the E0 gradient **updates item 16's
diagnosis** — this is a *local measurement*, the kind the Evidence policy demands:

| reason | n | E0 signature |
|---|---|---|
| no-edit (immediate stop) | 3 | 1 step, **0 tool-call rounds** — answers in prose, never acts |
| no-edit (churn + false success) | 1 | **8 tool-call rounds**, 4023 out-tok, ended `[runs grep]…"resolved"` but produced no diff |
| tests-failed (real attempt) | 2 | 10–16 rounds, **made edits**, tests didn't pass |
| timeout | 1 | 6 rounds, hit the 600s cap |
| oom | 1 | server crash mid-episode |

- **Degenerate "repeated-sentence" loop rate = 0/8.** The pathology item 16 named
  as *dominant* (planning sentence repeated 15–25×) **did not appear** at the 10-min
  cap here. (Caveat: the detector keys on repeated ≥12-char lines; the one churn case
  emitted `[runs grep]`×8 = 11-char lines, just under threshold — a known limit, not
  tuned away on n=1.)
- **`tool_use` events are unreliable** — 21627 had 8 tool-call rounds but emitted 0
  `tool_use` events. E0 now also records **`tool_call_rounds`** (`step_finish.reason`),
  the robust activity signal. *(instrumentation hardened post-run; the 8 artifacts
  were re-parsed in place — no re-run.)*
- **Lever-priority implication (revisit before L1).** The dominant local modes are
  **(a) not invoking tools at all (3/8)** and **(b) tool churn / wrong edits (3/8)** —
  NOT decode-loop repetition. So **L1 (anti-repetition sampling) is not clearly the
  top lever on this stack**; a tool-invocation / edit-application lever may dominate.
  This *updates* (does not refute) the admin trace-review diagnosis, which used a
  30-min cap and possibly different instances. Keep L1 in the slate but let the local
  gradient drive ordering. n=8, all sympy — widen the subset before over-fitting.

### Design decisions (resolved — plan-review 2026-06-23)

- **Measurement signal** → *add episode metrics now* (E0), independent of item 17;
  intermediate per-episode signals are the adopt/reject basis. *(user-confirmed)*
- **L1 location / legality** → *opencode-side sampling param*; `repetition_penalty`
  is a permitted category-7 sampling lever (like temp/top_p), not an engine change.
  Forward via the `sampling` block; verify the MLX endpoint honours it. *(user-confirmed)*
- **Enabling changes** → *re-implement fresh here* (E1, E2); admin versions don't
  transfer. The two prior `[x]` boxes were wrong for this repo. *(user-confirmed)*
- **Adopt/reject rule** → per-lever **primary episode metric improves vs baseline
  AND tool-call validity does not regress**; pass/fail secondary. *(default — not user-confirmed)*
  **⚑ REVISED 2026-06-23 (after L6's n=4→n=8 reversal): a single run cannot adopt a
  lever.** The tool-call path is non-deterministic even at `temperature=0`+fixed
  `seed` (MLX/Metal kernel nondeterminism — verified; no knob fixes it on this frozen
  stack). The improvement must clear run-to-run variance via **K≥3 runs/config**.
  Mechanical/deterministic levers (e.g. L3 edit-application) are exempt from K-runs
  only where the metric is provably draw-independent.
- **Test design** → **individual vs baseline first (attributable), then bundle the
  adopted winners** for the combined pass-rate effect. *(default — not user-confirmed)*
- **Definition of done** → all 5 levers have a documented adopt/reject decision
  **AND** the adopted bundle yields **≥1 full-harness pass** (0/8 → ≥1/8). *(default — not user-confirmed)*

### Measurement plan

- **Baseline:** `harness_configs/baseline.json` re-run at the new 10-min timeout (L0),
  with E0 metrics emitted.
- **Per lever:** one new `harness_configs/*.json` changing only that lever; run the
  full subset **K≥3 times** (a seed does NOT help — see methodology finding); compare
  its **primary episode metric** + tool-call-validity vs baseline, requiring the
  mean delta to exceed the run-to-run spread.
- **Prereq (blocking lever decisions): K-run harness support — DONE 2026-06-23.**
  `harness_eval.py run --repeats K` runs the subset K times (one ledger row per
  repeat, sharing a `repeat_group`), prints a per-config pass mean+spread, and the
  summary gains a **"K-run aggregates"** table (mean (min–max) of pass / dropped /
  made_edit / degen / steps via `_render_repeat_aggregate`; +2 selftest checks).
  This — not a seed — is what makes lever A/Bs comparable (`MLX_PROXY_SEED` is wired
  but verified NOT to give reproducibility on MLX/Metal). Keep K≈3 given 8–12 tok/s;
  multiplies item 19's GEPA budget by K. **Usage:** `run --config <c> --repeats 3`.
- **Bundle:** combine adopted levers; success = full-harness pass-rate 0 → >0.
- **Gate (every run):** tool-call round-trip validity must not regress vs baseline;
  `make check` (ruff + mypy + pytest) green for any harness code touched.

### Documentation

- `docs/opencode-local.md` — episode-metrics schema + each lever's result.
- `docs/harness-engineering-research.md` — cross-link L1 as a category-7 sampling lever.

### 17. Tiered validation harness  ▲ (was drafted as "12")

**Problem.** The harness is effectively **binary**: micro-suite passes, full
harness all-fails (item 16), no gradient in between — so you can't tell whether a
lever helped a little, or *which* task class / failure mode a change moved.

**Goal.** Four-tier difficulty system + metadata-tagged tests → a *gradient* score
and a failure-mode breakdown. **Reuse item 16's 7-failure-mode taxonomy** as the
`failure_category` enum so the two efforts share one vocabulary.

- [x] **17.1 Define four difficulty tiers** — DONE. Unified ladder over BOTH
      harnesses (decision A): **T1** micro single tool-call · **T2** micro
      multi-step + micro-edit · **T3** single-file localized real fix · **T4**
      multi-file/multi-site real fix. Rubric + taxonomy + report schema documented
      in `docs/tiered-harness.md`. Code: `GLOBAL_TIERS` / `MICRO_TIER_MAP`.
- [x] **17.2 Per-test metadata** — DONE. `InstanceSpec` gained `tier`, `n_files`,
      `needs_search`, `needs_bash`, `expected_tool_seq` (static, manifest-frozen);
      `failure_category` is **derived per-episode** (decision B), not a static tag —
      shared `classify_failure()` maps `reason` + E0 metrics → the item-16 7-mode
      taxonomy (`FAILURE_CATEGORIES`). Wired into both `harness_eval.py` and
      `harness_micro.py` (micro `TestResult.failure_category`); `parse_episode_jsonl`
      now records `errored_tools` so grep-parse vs edit-mismatch separate.
- [x] **17.3 Build the tiered set** — DONE. New offline `tier` subcommand
      (`assign_tier`) buckets each instance from its cached gold patch + F2P set
      (1 file·1 hunk·1 F2P ⇒ T3, else T4) and writes metadata back into
      `scripts/harness_eval_subset.json` (idempotent, preserves `frozen_at`). Result
      on the frozen sympy-8: **T3=3** (21614/12481/21627), **T4=5**. (All 8 are
      single-file, so the T3/T4 split is by hunks/F2P — the within-SWE-bench
      gradient; the easy *passable* rungs are T1/T2 from the micro-suite.)
- [x] **17.4 Per-tier × per-failure-mode scoring** — DONE. `tier_breakdown()` +
      `build_tier_report()` give per-config per-tier pass/total + a derived
      failure-mode histogram, replacing the single pass/fail number.
- [x] **17.5 Structured end-of-loop report** — DONE. `_render_tier_report` (folded
      into `summary.md`) + `write_tier_report` → `tier-report.jsonl` next to the
      ledger (per-config: per-tier pass_rate, delta_vs_baseline, failure_histogram).
      New `report` subcommand renders on demand. **Cheap by construction** — pure
      aggregation over the shared ledger, no re-run — so item 19 can read it as a
      fitness signal. Historical rows lacking on-row tiers fall back to the manifest
      by `instance_id`, so the gradient is retroactive (existing baseline now shows
      T3 0/3, T4 0/5 with per-tier histograms).

**Design decisions (resolved — user-confirmed 2026-06-23):**
- **Tiered-set source** → *unify the two existing harnesses* into one 4-tier
  ladder (micro = T1/T2, SWE-bench = T3/T4) rather than authoring a new easy
  real-fix tier or only bucketing the 8. The passable rungs a weak model can clear
  come from the synthetic micro-suite; SWE-bench supplies the hard rungs. Lowest
  new work, reuses the validated shared ledger, no online curation.
- **`failure_category` semantics** → *derived per-episode* (observed `reason` + E0
  metrics, histogrammed), NOT a static per-test tag. Static metadata is only
  `tier`/`n_files`/`needs_search`/`needs_bash`/`expected_tool_seq`.
- **Scope** → *everything end-to-end* (17.1–17.5 all landed offline).

**Status (2026-06-23): item 17 COMPLETE — all 5 sub-items done; `make check`
green; both selftests OK (harness_eval +18 new item-17 checks).** The harness now
emits a per-tier gradient + failure-mode histogram instead of a flat 0/8. Unblocks
item 19 (a cheap `tier-report.jsonl` fitness signal exists) and shares the
`failure_category` vocabulary with item 18's trace-detection targets.
**Caveat for the next run:** the new T3/T4 split is recorded on each *new* ledger
row, but a *fresh* `run` is still needed to populate per-tier numbers under any
lever config — the retroactive split shown today is derived from the one
historical baseline row (still 0 across T3+T4).

### 18. Improvement-recommender agent  ▲ (was drafted as "13")

**Goal.** A data-driven agent that reads **Jaeger traces** (the OTel spans the
repair proxy + `patch_otel_plugin.py` already emit) + **prior work** (claude-mem
observations, the `docs/` research, item-17 reports) and proposes ranked harness
improvements. Note: item 16 already proved trace-review *by hand* finds the real
defects — this item **automates** that loop.

- [ ] **18.1 Trace-ingestion path** — structure Jaeger spans into per-session
      tool-call sequences, retries, malformed-call events, latency, per-turn context
      size, **and degenerate-loop detection** (the item-16 signature).
- [ ] **18.2 Recommendation surface** — suggests **new skills / subagent patterns /
      hooks / prompt improvements**, each tied to trace/report evidence.
- [ ] **18.3 Close the loop** — recommendations become item-17 configs to A/B; the
      structured report (17.5) measures whether each one moved a tier/failure-mode.

### 19. Structured prompt-optimisation (GEPA)  ← deep-research item (was drafted as "14")

**Goal.** Apply a structured optimiser to the harness's text levers (system/agent
prompts, tool descriptions, skill docs). **GATED:** do not start until item 16
lands and the full harness gives a non-zero, non-degenerate signal — item 16
showed prompt-phrasing changes don't move a harness that's failing mechanically.

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
- [ ] **19.2 Feasibility filter.** (a) Decide whether a larger/cloud model is the
      **reflector/proposer only** while fixed local Gemma stays optimisee+evaluator
      (loop may be online even though *serving* is offline); (b) size GEPA's total
      rollout budget vs wall-clock at 8–12 tok/s; (c) confirm item-17's harness is
      cheap enough as the fitness function.
- [ ] **19.3 Prototype GEPA** against the item-17 harness as fitness function. **Must:**
      shape the fitness fn to **penalise tool-call regressions** (use item-17
      failure-mode metadata), keep the frozen baseline, reject any candidate that
      regresses the tool-call floor. Fall back to CAPO/OPRO if rollout cost is infeasible.

### 20. Planning-first phase / orchestration topology  ← deep-research item

**Goal.** Decide whether to add a **dedicated planning phase before execution**,
and how much orchestration machinery is worth it for a weak local model. **GATED
behind item 16** — a planning phase interacts directly with the degenerate-loop fix
(see risk below). Opencode mechanics confirmed: it natively ships a read-only
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
      stack, or did the literature mislead? Gated behind item 16 (needs E0 metrics).

### 21. Sandboxed code-execution for parallel/chained tool calls  ← deep-research item

**Goal.** Investigate whether driving tool calls through a **code-execution sandbox**
(the "code mode" / "code execution with MCP" pattern — e.g. the **Monty** Python
sandbox, Cloudflare Code Mode, Anthropic's code-execution-with-MCP) lets the agent
**batch, chain, and parallelise tool calls in a single rollout** instead of the
current one-tool-call-per-round-trip flat ReAct loop. **Why it matters here:** every
tool call currently returns to the main agent and forces a fresh decode pass; at
**8–12 tok/s** those round-trips dominate wall-clock. If the model can emit one code
block that runs N tool calls (sequential *and* parallel) in the sandbox and returns
only the final result, we cut decode passes — potentially the single biggest
wall-clock lever on a slow local model. **Risk to weigh:** asking a weak 4B model to
*write correct orchestration code* may be harder than emitting one tool call, and
could induce new failure modes (cf. item-20's "full thinking collapses the 4B");
sandbox infra adds offline/16 GB-M1 constraints. **GATED behind item 16** (needs E0
instrumentation to measure round-trip cost and any new failure modes).

- [x] **21.1 Deep-research survey** — **DONE** (2026-06-23, run `wf_42940d55-80e`;
      18 sources, 85 claims → 19 confirmed / 6 refuted). Findings + citations:
      `docs/sandbox-codeexec-research.md`.
      **Verdict:** the **mechanism is sound and "Monty" is deployable offline**, but the
      **make-or-break weak-model question fails on current evidence** — prototype only
      after measuring local code-gen success.
      - **Monty = Pydantic Monty** (3-0): a from-scratch Python-subset bytecode VM in Rust,
        in-process, **~5 MB / ~µs start**, zero-access-by-default, MIT, **fully offline** —
        the lowest-overhead, only-fully-offline-in-process option (vs Pyodide ~2 s,
        Cloudflare workerd cloud/JS closed-beta, E2B/Firecracker cloud/microVM). But it's
        **alpha (v0.0.18, Python subset, codemode integration unshipped)** — clashes with
        the "frozen stack" rule; the *pattern* transfers, the package is a moving target.
      - **Round-trip elimination is real** (3-0): control flow + intermediate state run
        *inside* the emitted code (zero model calls); large results filtered in-sandbox.
        **But published wins are modest and frontier-sourced** — weather agent 4→2 calls,
        12.2 s→9.1 s, $0.019→$0.017 on **Sonnet 4.5**; the 99.9%/98.7% figures are static
        API-exposure footprint, **not** end-to-end (real end-to-end ~32–81%); Anthropic's
        150k→2k figure was **REFUTED (0-3)**.
      - **❌ Weak-model (≤7B): no positive evidence, substantial against** (3-0). Documented
        **"structure tax"** — small models can't juggle syntax + orchestration; HF
        recommends structured code-agents only for **"32B+ or frontier."** SLM pass@1 <0.10;
        CodeAct's "20%" is GPT-4 (Mistral-7B 0% on M3ToolEval). Multiple pro-weak-model
        claims **actively refuted** (0-3 / 1-2). **One nuance FOR us:** the tax condemns
        *JSON-wrapped* code; **plain markdown code blocks** (closer to Monty usage) are
        **less penalized** — the one thread worth pulling.
      - **Stack upside:** at 8–12 tok/s each round-trip is far costlier than on cloud, so
        savings are proportionally **larger here — IFF the model writes correct code.**
      **[lit-only]** per the Evidence policy: citation-checked, not measured here. The
      decisive number — Gemma-4-E4B orchestration-code-gen pass@1 — is **unmeasured**;
      21.3 is its local validation, and 21.2's first experiment must measure it before
      committing (see `docs/sandbox-codeexec-research.md` open questions).
- [x] **21.2a Decisive experiment — local code-gen pass@1 — DONE, GATE PASSED**
      (2026-06-23). Built `scripts/codegen_probe.py`: model-agnostic probe, 6 frozen
      orchestration tasks (chain 2–5 mock host tools + loop/conditional → one **markdown**
      code block → `result`), graded by execution-against-mocks (hardcoding rejected; tool
      calls counted at runtime). Ran local Gemma-4-E4B vs the online control
      `opencode/big-pickle`, k=3.
      **Result: local-gemma pass@1 = 1.0 (18/18) === bigpickle pass@1 = 1.0 (18/18), Δ=0.0.**
      **Size is NOT the blocker at this tier** — the 4B emits correct orchestration code as
      reliably as a frontier model. This **locally REFUTES 21.1's `[lit-only]` "structure
      tax" negative** (markdown, not JSON-wrapped, as predicted). Ledger:
      `scripts/codegen-probe.jsonl`; full writeup: `docs/sandbox-codeexec-research.md`
      (Empirical addendum). **Limits:** tasks are simple/moderate (≤7 calls, single-level
      control, ≤6 tools, no parallelism/nesting/error-handling); k=3; restricted `exec` not
      Monty's subset; clean prompt. So this is **green-light-to-prototype, not a closed win.**
- [x] **21.2b Harder tier + real Monty engine — DONE, gate holds at complexity** (2026-06-23).
      Extended `scripts/codegen_probe.py` with a **hard tier** (5 tasks: nested loops, `try/except`
      error-handling, argmax, filter chain, sort/select) over the **full 13-tool menu** (with
      distractors → forces tool selection), wired the real **`pydantic_monty` v0.0.18** engine
      (`external_functions` + `max_duration_secs`), and added `--engines exec monty` to grade the
      SAME output through both VMs apples-to-apples. k=5. Writeup: `docs/sandbox-codeexec-research.md`
      (Empirical addendum 2).
      **Results:**
      - **Gemma-4-E4B hard tier: exec 1.0 (25/25) AND monty 1.0 (25/25).** Combined with 21.2a
        (base 18/18), local model is **50/50 under exec across both tiers** — the structure tax
        still doesn't bite. Gate holds at higher complexity.
      - **Monty's alpha dialect DOES tax — but it hits the FRONTIER model, not Gemma.** big-pickle
        hard: exec 1.0 vs **monty 0.84** (Δ=−0.16, all failures on `longest_file`). Root cause
        (reproduced): Monty v0.0.18 rejects `max(items, key=lambda x: <host-tool call>)`
        ("external functions not yet supported") and `dict.get()`. **Gemma scored monty 1.0 because
        it writes plainer explicit loops, not idiomatic one-liners** — its simpler style dodges the
        dialect gaps that bite the frontier model. Counterintuitive but mechanistically clear.
      - **Bonus:** the hard tier caught a grader bug (restricted `exec` lacked `Exception`, unfairly
        failing `try/except` code) — fixed.
      **Takeaways for 21.3:** (1) code-mode is viable on this stack even with a plain restricted
      `exec` (Gemma 1.0 both tiers) — Monty is optional, adds only isolation; (2) if using Monty,
      the dialect risk is currently *unrealized for Gemma* but fragile (style-dependent) — cheap
      mitigation = a prompt note preferring explicit loops / avoiding `max(key=…)` over tool calls
      and `dict.get`; (3) k=5 is strong-not-tight — raise k for final sign-off.
- [x] **21.3 End-to-end round-trip A/B — DONE (prototype), HYPOTHESIS CONFIRMED** (2026-06-23).
      Built `scripts/codemode_ab.py`: same multi-step tasks run two ways on the live local model —
      **flat ReAct** (one tool call per decode pass, JSON action protocol) vs **code-mode** (one
      sandboxed code block, ~1 pass). Raw MLX endpoint (:8081) to avoid the proxy's tool-call
      parser confounding the text protocol. 6 round-trip-heavy tasks, k=2. Writeup:
      `docs/sandbox-codeexec-research.md` (Empirical addendum 3).
      **Result (mean per task):**
      | arm | pass@1 | passes | wall_s | tokens |
      | --- | --- | --- | --- | --- |
      | flat ReAct | **0.333** | 10.67 | **245 s** | 8301 |
      | code-mode  | **1.0**   | 1.0   | **40 s**  | 715  |
      → code-mode **−90.6% decode passes · −83.5% wall-clock · −91.4% tokens · +0.667 pass@1**.
      ReAct **failed by non-termination on 4/6 tasks** (item-16's no-tool-stop/churn pathology);
      code-mode was **12/12 correct, always 1 pass**, and won even on the 2 tasks ReAct finished.
      The wall-clock gap exceeds the pass-count gap because flat ReAct re-prefills a growing context
      (returned file bodies re-enter every turn) while code-mode prefills once and never shows the
      model the intermediate data. **Caveats:** proxy harness with mock tools (not real opencode);
      ReAct's 0.333 is a lower bound (JSON protocol may be harder than native tool-calls — but
      item-16 found the same churn natively); n modest (k=2×6) but effect huge and consistent.
- [x] **21.4a Wire code-mode into real opencode — DONE & live-verified** (2026-06-24).
      Shipped `scripts/codemode_exec.py` (real executor: the validated sandbox bound to REAL
      host-tools `read_file`/`read_lines`/`list_files`/`glob`/`grep`, `bash`/`write_file` opt-in,
      paths can't escape root, JSON envelope) + `.opencode/tools/codemode.ts` (opencode tool →
      `Bun.spawn` → executor; model writes Python, runs out-of-process). Writeup:
      `docs/sandbox-codeexec-research.md` (Empirical addendum 4).
      **Verified end-to-end:** executor selftest → real-repo run (9 host-ops in one pass) →
      Bun↔Python wiring → `opencode serve` registration (codemode loaded, no errors) → **live
      capstone: local Gemma invoked `codemode` NATIVELY in one tool call** through the full agent
      loop + repair proxy (one glob + 8 reads, one decode). The round-trip collapse works in
      production.
      **Bug caught & fixed by the capstone:** model used `read_lines(p, 1, None)` expecting
      1-indexed lines; the tool was 0-indexed slice → off-by-one. Fixed to 1-indexed inclusive
      (matches the `read` tool's gutter). Lesson: host-tool signatures must match NL intuition —
      a class of footgun only a real run surfaces.
- [x] **21.4b Production A/B on real opencode — DONE (directional), 21.3 win TEMPERED** (2026-06-24).
      Built `scripts/codemode_prod_ab.py` (baseline vs codemode-enabled opencode on the
      `harness_micro_fixtures` repo, reusing harness_micro's config/episode/transcript machinery).
      3 tasks, k=1. Writeup: `docs/sandbox-codeexec-research.md` (Empirical addendum 5).
      **Result — the win is REAL but TASK-DEPENDENT, not the proxy's 5×:**
      | task | baseline | codemode |
      | --- | --- | --- |
      | count_lines | 1 call (`bash wc`), 94s ✓ | 1 call, 72s ✓ (−24%) |
      | def_count | 4 calls (`grep`×4), 342s ✓ | 2 calls, 150s ✓ (−50% calls, −56% wall) |
      | find_clamp | timeout/0-calls ✗ | timeout/0-calls ✗ |
      **Decisive correction:** real opencode has **`bash`**, itself a "code mode" — the model
      self-batched `count_lines` into one `wc` call, so codemode's edge shrank to ~24%. **21.3's 5×
      was inflated by a bash-less mock baseline.** codemode still clearly wins when the model does
      NOT self-batch (def_count: grep×4 → 2 calls). And it does **NOT** fix the degenerate-loop
      (find_clamp timed out on BOTH arms, 0 calls — item-16 pathology). **Verdict:** keep `codemode`
      enabled (never lost, won the non-self-batched case), cite 21.3's 5× as a *bash-less upper
      bound*, and don't expect a headline pass-rate move until item-16's degenerate-loop is fixed.
      **Caveats:** k=1 directional (find_clamp double-timeout needs more samples); 3 small tasks;
      transcript-substring grading.
- [ ] **21.4c (optional follow-up) — firm up + find code-mode's real niche.** Raise k (≥5) and add
      tasks where bash is a poor fit (structured/multi-step parsing, conditional logic on file
      contents) — the regime where codemode should separate cleanly from a `bash` baseline. Re-run
      after item-16's degenerate-loop fix lands (so find_clamp-style tasks don't just time out).
      Only then make the final adopt/reject + decide whether to enable `codemode` by default in the
      global config. **Gated behind item 16.**

---

## Notes / open questions

- **Sequencing.** 16 → 17 → (18, 19). Item 16 is the prerequisite: a mechanically
  broken full harness can't give signal for 17's tiers or 19's optimiser.
- **Shared failure vocabulary.** Item 16's 7-mode taxonomy = item 17's
  `failure_category` enum = item 18's trace-detection targets. Define once.
- **Optimiser-cost tension.** Any search-based optimiser (GEPA/CAPO/OPRO) needs many
  candidate evals; each is a slow local harness run → item 17.5 must be a fast,
  cheap inner-loop fitness function.
- **Reliability floor.** No change may regress tool-call validity; every candidate
  passes the tool-call round-trip check before it scores.
