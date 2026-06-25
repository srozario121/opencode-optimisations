# Changelog — opencode-optimisations

Completed work, moved out of `TODO.md`. Newest milestones first within each
group. Items keep their original ledger numbers (1–15) for cross-reference with
claude-mem memory and the `docs/` research.

> **History note.** This repo was *extracted* (item 15) from a larger personal
> toolkit (`admin`), where the original 2,475-line TODO.md (items 1–16) lived.
> That ledger did not come across — items 1–16 were reconstructed from claude-mem
> cross-session memory so the numbering stays continuous. Items 1–15, **16, 17, 21,
> and 22** are recorded here; the open work (items 18, 19, 20) remains in `TODO.md`.

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

## Done (items 16, 17, 21, 22)

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
