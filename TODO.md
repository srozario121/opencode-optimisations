# TODO — opencode-optimisations

The repo's running work-ledger. Item **16** is the open, diagnosed bottleneck
(carried over from the original ledger); items 18–20 are the open work from the
2026-06-22 planning session. **Completed items 1–15, 17, and 21 now live in
`CHANGELOG.md`** (item 21's optional 21.4c follow-up is still open below).

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
- [x] **RESOLVED 2026-06-24 — the wire link is verified.** opencode's
      `@ai-sdk/openai-compatible` provider **DOES serialise the non-OpenAI
      `repetition_penalty` key top-level into the wire request body** (not dropped,
      not relocated). Verified non-disruptively (no touch to the live 8080/8081
      stack): a throwaway capture server on :8099 + one `opencode run` against an
      `opencode.json` mirroring `apply_levers` with `repetition_penalty` under the
      model options → captured body keys `[max_tokens, messages, model,
      repetition_penalty, stream, stream_options, temperature]`, `repetition_penalty
      = 1.3` at top level (alongside `temperature`). Probe: `scratchpad/wire_check.py`.
      **⇒ The full L1 chain is now end-to-end verified** (config `sampling` →
      `opencode.json` model options → wire body top-level → mlx-lm 0.31.3 sampler,
      which was already source-verified to read it). An L1 number can now be trusted.
      Runnable config landed: `scripts/harness_configs/rep-penalty.json`.

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
> **⚑ UPDATE 2026-06-24 (micro-gradient A/Bs, then higher-K + SWE confirm):**
> **L6 (no-think) — CONDITIONAL wall-clock lever, NOT adopted as a default.** Looked
> like the strongest arm at micro K=3 (1.0×3, −42% wall-clock) but the K=6 confirm
> broke the perfect ceiling (r6 0.882, genuine miss) and the **SWE regression check
> found a real regression** (real edit-attempts 12→4, +10% wall-clock). It helps pure
> executor turns, **hurts reasoning-dependent fixes** → only viable behind per-turn
> gating this frozen stack lacks. **L1 — rejected as a pass-mover** (safe, holds
> ceiling; possible variance-reducer). Net: **no cheap config/proxy lever moves the
> SWE pass-rate; the T2→T3 capability wall stands. L5 (loop) is the last unrun lever.**

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
      - [x] **REVISITED + CONFIRMED on the item-17 gradient (2026-06-24) — VERDICT:
        a CONDITIONAL wall-clock lever, NOT a global default; it REGRESSES real fixes.**
        Ran on the micro gradient (first tier with headroom) AND a SWE regression
        check, both vs the K=3 controls. Proxy restarted with `NO_THINK=1`
        (user-authorized; capture confirmed `reasoning_present=False` on tools turns →
        lever genuinely engaged), then restored to `NO_THINK=0`. Configs in repo
        (`micro-no-think`, `no-think`).
        - **Micro K=3 looked like a clean win (1.000 ×3, −42% wall-clock) — but the
          higher-K confirm BROKE the "perfect ceiling".** At **K=6**: 1.0×5 then **r6
          0.882** (a genuine wrong-answer — `t1-nav-surcharge` ran 5 tool calls, 51 s,
          scored 0/4, NOT a timeout). So L6 micro mean **0.980 (spread 0.882–1.0)** vs
          baseline **0.961 (0.941–1.0)** — higher mean but **wider spread + a WORSE
          worst-case** than baseline. The K=3 "zero-variance dominance" was **small-n
          optimism** (exactly the n=4→n=8 pattern the methodology warns of). Pass-rate
          edge is now within noise. **Only the −42% wall-clock/task (42.7 vs 73.3 s) is
          robust** — mechanically guaranteed (no reasoning-phase decode).
        - **SWE regression check (K=3, 24 episodes each) — REAL REGRESSION found:**
          | failure mode | baseline | no-think |
          |---|---|---|
          | tests-failed (*real* attempt) | **12 (50%)** | **4 (17%)** |
          | no-edit | 5 (21%) | **11 (46%)** |
          | timeout | 7 (29%) | 9 (38%) |
          | pass | 0/8 | 0/8 |
          | wall-clock | 7997 s | **8768 s (+10%)** |
          No-think makes the model **attempt real fixes far less often** (tests-failed
          12→4), reverting them to no-edit/timeout, and runs **slower** on SWE (more
          spinning → 9 vs 7 timeouts). Pass stays 0/8 (capability wall) but the
          **failure-mode quality degrades**.
        - **Synthesis (validated locally, not just lit):** NO_THINK helps **pure
          executor/tool turns** (micro: faster, marginally better) but **hurts
          reasoning-dependent turns** (SWE real fixes: fewer real attempts, slower) —
          exactly item-20's "executor thinking hurts / planner thinking helps", now
          measured on this stack. **⇒ Do NOT default NO_THINK ON globally** — a blanket
          toggle is net-negative because the harness can't tell an executor turn from a
          fix-planning turn. It would only pay off behind **per-turn/role gating** (apply
          to mechanical tool turns, keep thinking for fix turns) — which this frozen
          stack has no clean seam for. **Lever kept available (toggle), NOT adopted.**
        - (Standalone L1 wire check is independently RESOLVED — see E-sampling.)
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
- [x] **L1 — Anti-repetition sampling** (`repetition_penalty`, NOT
      `no_repeat_ngram_size` — mlx-lm drops the latter; see E-sampling).
      **MEASURED on the micro gradient (2026-06-24, K=3) — NOT ADOPTED as a
      pass-mover; HOLDS the ceiling; one variance lead.** Wire path VERIFIED
      (2026-06-24, see E-sampling); configs runnable
      (`scripts/harness_configs/rep-penalty.json` for SWE,
      `scripts/harness_micro_configs/micro-rep-penalty.json` for micro;
      `repetition_penalty=1.1` + `repetition_context_size=20`).
      Ran on the micro suite (the only tier with headroom — SWE degen rate was 0/8)
      vs the micro-baseline K=3 control:
      - **micro-baseline K=3:** checks 34/32/32 → mean **0.961** (spread 0.941–1.0);
        T1 4/4, T2 4/6 (no-edit×2 at task level), T3 4/4.
      - **micro-rep-penalty K=3:** checks 33/33/33 → mean **0.971** (spread **0**);
        T1 4/4, T2 5/6 (no-edit×1), T3 4/4.
      - **Adopt/reject:** the +0.010 check-level mean delta falls **inside** the
        baseline spread → does **NOT** clear run-to-run variance → **NOT adopted**
        per the K-run rule. Tool-call validity did **not** regress (T1/T3 held 4/4;
        the lone miss was `t2-find-format` 2/3, a check-content near-miss, not a
        tool-call failure). So anti-repetition is **safe (holds the ceiling) but not
        a pass-rate lever here** — consistent with its DEMOTED status (target mode
        absent on this stack).
      - **Lead worth a dedicated test:** L1 **collapsed run-to-run variance to ZERO**
        (0.9706 ×3, identical tier histogram each repeat) vs baseline's 0.94–1.0
        swing — mechanistically it dampens the sampling-path stochasticity that
        forced K-runs (item-16 methodology finding). n=3 is a hint not proof; if a
        *variance-reducer* is wanted (to cut future A/B K), test `repetition_penalty`
        in a variance-focused run (more repeats, report std not just mean).
- [x] **L0 — Re-baseline** at the 10-min timeout. **DONE 2026-06-23: 0/8** — see
      the "⚑ L0 baseline result" subsection below for the full E0 gradient that
      drove this reprioritization.

### ⚑ Tiered baseline result — the gradient now exists (2026-06-24)

First full **tiered** baseline under the post-item-17 / post-codemode harness (user
chose "tiered baseline first"). Micro K=3 + SWE K=3, then `report`. **This is the
non-flat gradient item 16 was missing** — a weak model passes the easy rungs and
falls off a cliff exactly at the synthetic→real boundary:

| tier | what | result |
|---|---|---|
| **T1** | single tool-call (synthetic) | **4/4 ✓** |
| **T2** | multi-step + micro-edit (synthetic) | **~4–6/6 ✓** (K=3: r1 1.0, r2/r3 0.94 — one T2 task drops 2 checks; mild MLX nondeterminism) |
| **T3** | single-file real SWE fix | **0/3 ✗** |
| **T4** | multi-file/reasoning SWE fix | **0/5 ✗** |

- **SWE K=3 = 0/8 every repeat (pass mean 0.0, spread 0–0)** — confirms the floor is
  rock-stable, not a single-draw artifact. Any lever delta must clear a 0-wide spread.
- **SWE failure histogram: `tests-failed×5`, `no-edit×2`, `timeout×1`.** The shift vs
  the L0 baseline (which had only `tests-failed×2`) is notable: **more instances now
  reach a real edit attempt** (5/8 produce a wrong-but-real fix) — i.e. the shipped
  harness (edit.ts / codemode / L3 fixes) is getting the model to *act*, but the fixes
  are **wrong**. The remaining gap is **model capability on real fixes, not harness
  mechanics**.
- **Verdict reinforced:** the cliff is T2→T3, and it is a *capability* wall. Micro
  rungs (T1/T2) are the gradient any lever / GEPA must optimise on, because they are
  the only tiers where a non-zero signal can move. The SWE tiers measure ceiling, not
  harness polish.
- Ledger: `~/.config/opencode-optimisations/harness-eval/{ledger,tier-report}.jsonl`;
  configs `micro-baseline` + `baseline` rows under labels `micro-baseline-tier-r{1,2,3}`
  / `baseline-tier-r{1,2,3}`.

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

### 18. Improvement-recommender agent  ▲ (was drafted as "13")

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

- [ ] **18.1 Episode-corpus ingestion** (`harness_eval.py recommend`, part 1) — load
      the on-disk `opencode.jsonl` + `ledger.jsonl` + `tier-report.jsonl` and structure
      per-episode: tool-call sequence, retries, errored-call events, latency-to-first-tool,
      output-tokens, steps-to-first-edit, **and degenerate-loop signal** (all already
      produced by `parse_episode_jsonl` — reuse it, do not reimplement). Aggregate by
      `failure_category` × tier across runs. **No Jaeger dependency.**
- [ ] **18.2 Recommendation surface (Claude Code / Opus 4.8 proposer)** — feed the
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
- [ ] **18.3 Close the loop (the decisive validation)** — take the top emitted
      *runnable* config(s), run via `harness_eval.py run --repeats K` (K≥3, per the
      item-16 methodology — single runs can't adopt on this nondeterministic stack), and
      record via `report` whether each moved a tier/failure-mode vs baseline. Each
      proposal stays **[tool-proposed]** until this local A/B closes it.
- [ ] **18.0 (validation prereq) Known-answer backtest — RECALL *and* PRECISION.**
      Build a **labelled ground-truth set** from the pre-fix corpus (`baseline-L0-*`,
      `nothink-*`): each known item-16 defect tagged to its instance(s) —
      dropped-output/thinking-stop on 12481/11400/19007, edit gutter/whitespace on
      15345/13043, the 19007 364-round loop. Run 18.1 (digest) → 18.2 (Opus-4.8
      proposer) over that corpus and require: **(a)** it surfaces those true modes on
      their instances (**recall**), **AND (b)** it does not over-flag — spurious
      recommendations are scored against the taxonomy (**precision**), so a recommender
      that flags everything **fails**. Because the proposer is an LLM, score **several
      proposer samples** and require the bar on the **majority** (report the spread).
      This certifies the *recommender itself* before any novel proposal is trusted.
- [ ] **`make check` (ruff + mypy + pytest) green** for the **Layer-1** `recommend`
      digest (the deterministic part): selftests cover the ingestion (against a fixture
      episode jsonl), the digest aggregation, and the config **schema-validation** of a
      proposer-emitted config (incl. the `needs-implementation` split). The **Opus-4.8
      proposer (Layer 2) is validated by the 18.0 backtest, not unit tests** — it is
      non-deterministic, so its quality gate is recall/precision over several samples,
      not a fixed assertion.

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

- [ ] **Update** `docs/opencode-local.md` (master doc) — record item 18 as the
      two-layer automated trace-review recommender (deterministic `harness_eval.py
      recommend` digest → Claude Code / Opus-4.8 proposer), its input corpus (episode
      jsonl/ledger, NOT Jaeger), and its config-emitting output.
- [ ] **Update** `docs/tiered-harness.md` — note the recommender consumes
      `tier-report.jsonl` + the per-episode `opencode.jsonl` and emits item-17 configs.
- [ ] **Update** `docs/jaeger-tracing.md` — clarify Jaeger is a *live human debugging*
      aid; the recommender uses the durable jsonl corpus instead.
- [ ] **Update** `CHANGELOG.md` only when item 18 reaches a closed outcome.

### 19. Structured prompt-optimisation (GEPA)  ← deep-research item (was drafted as "14")

**Goal.** Apply a structured optimiser to the harness's text levers (system/agent
prompts, tool descriptions, skill docs). **GATED:** do not start until item 16
lands and the full harness gives a non-zero, non-degenerate signal — item 16
showed prompt-phrasing changes don't move a harness that's failing mechanically.

> **⛔ PRECONDITION (checkable — 19.2 work may not begin until BOTH ticks land).**
> Item 19 is **BLOCKED until: (1)** item-16 **L5** (`doom_loop` policy — the last
> unrun item-16 lever) reaches a **recorded adopt/reject verdict in `TODO.md`**,
> **AND (2)** the **T2 gate-check passes** (19.2 task below). Either failing leaves
> item 19 gated; record "**no climbable signal yet**" and stop. Rationale: item-16's
> current evidence is a **stable 0/8 T3/T4 capability wall** (not harness mechanics),
> and the only tier with real headroom is the synthetic **T2** rung — so GEPA only has
> somewhere to climb if T2 still shows a non-saturated, non-noise gradient after L5.

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
- [ ] **19.2 Feasibility filter (gate + budget — runs only once the L5 precondition
      tick lands).** Settles whether 19.3 may run AT ALL and, if so, with what budget:
  - [ ] **(gate) T2 climbable-gradient check.** Re-measure baseline **T2 at K≥3** and
        apply the unlock rule: pass iff `T2_mean` strictly inside `(floor, ceiling)`
        **AND `(1.0 − T2_mean) > K-run spread`**. **Fail ⇒ item 19 stays gated**
        ("no climbable signal yet"); do not proceed to 19.3. (This is precondition (2).)
  - [ ] **(timing) Per-T2-rollout wall-clock micro-task.** Measure the **median T2
        rollout wall-clock at K=3 on this machine**; from it **compute the
        candidate-budget N and the abort wall-clock ceiling** for 19.3. This timing is
        the concrete deliverable that unblocks 19.3's budget — 19.3 cannot size its run
        without it.
  - [ ] **(reflector) Confirm the reflector wiring is loop-only.** Verify a
        larger/cloud reflector consumes only captured local traces and emits only text
        levers into the config bundle, with a "serving-offline" assertion; the local
        Gemma remains optimisee + evaluator.
  - [ ] **(fitness) Confirm `tier-report.jsonl` is cheap enough as the inner-loop
        fitness read** and that the `score = T2_frac − λ·penalty` scalar + T1 hard gate
        compute correctly from it.
- [ ] **19.3 Prototype GEPA** against the item-17 harness as fitness function — **runs
      only after both 19.2 gate ticks pass.** Implements the resolved design:
  - [ ] Fitness = **`T2_frac − λ·(rise in no-edit+error+catastrophic-edit)`** with **λ
        large** (any floor regression ⇒ negative vs baseline) and the **T1 hard gate**
        (reject on T1 drop); T3/T4 reported, weight 0. Keep the **frozen baseline**.
  - [ ] **Cloud-reflector-only** loop (serving offline), **T2-only budget**
        (`≤N × K=3`, abort at the 19.2 wall-clock ceiling → CAPO/OPRO fallback).
  - [ ] **Counter-arm:** a **single fixed GEPA candidate vs frozen baseline, K≥3** —
        record whether prompt/skill optimisation moves T2 at all (validates item-16's
        negative claim instead of assuming it).
  - [ ] **Offline re-validation before adopt:** rerun the adopted candidate
        **reflector-disconnected, fully offline**; adopt iff T2 stays within the K-run
        spread of the online score AND the floor holds.
  - [ ] **Fallback:** CAPO/OPRO via `promptolution` (offline-native) on **abort only**,
        same T2 scalar + λ floor + K≥3.
  - [ ] **Valid outcomes (all closed, per Evidence policy):** adopt a candidate; OR
        "GEPA/CAPO does not move T2 here" (negative validated locally); OR "infeasible
        at this tok/s under the budget". Any not-yet-run conclusion stays **[lit-only]**.
  - [ ] **`make check` (ruff + mypy + pytest) green** for any harness/optimiser code
        added; selftests cover the fitness scalar + λ penalty + T1-gate logic.

### Documentation (item 19)

- [ ] **Update** `docs/structured-optimisation-research.md` — append the resolved 19.2/
      19.3 design (T2-only fitness scalar, λ floor, cloud-reflector-loop-only +
      offline re-validation, tier-scoped budget, CAPO/OPRO fallback) and, once run, the
      local-validation result that replaces the **[lit-only]** GEPA verdict.
- [ ] **Update** `docs/tiered-harness.md` — document `tier-report.jsonl` used as the
      GEPA fitness read and the `score = T2_frac − λ·penalty` + T1-hard-gate definition.
- [ ] **Update** `docs/opencode-local.md` (master doc) — record item 19's adopt/reject/
      infeasible outcome as a lever result once 19.3 closes.
- [ ] **Update** `CHANGELOG.md` only when item 19 reaches a closed outcome (mirrors the
      item-17/21 pattern).

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

### 21. Sandboxed code-execution ("code mode") — 21.1–21.4b DONE (see `CHANGELOG.md`)

**The bulk of item 21 is complete and recorded in `CHANGELOG.md`** (21.1 survey →
21.2a/b local code-gen gate PASSED → 21.3 round-trip A/B → 21.4a wired into real
opencode → 21.4b production A/B). **Net so far:** code-mode is viable on this stack
(Gemma-4-E4B writes correct orchestration code, pass@1 1.0 both tiers — the lit
"structure tax" does not bite here); `codemode` is **kept enabled**; the 21.3 5× win
is **tempered** by real opencode's `bash` (the model self-batches), and code-mode
does **not** fix the item-16 degenerate-loop. Only the optional follow-up remains:

- [ ] **21.4c (optional follow-up) — firm up + find code-mode's real niche.** Raise k (≥5) and add
      tasks where bash is a poor fit (structured/multi-step parsing, conditional logic on file
      contents) — the regime where codemode should separate cleanly from a `bash` baseline. Re-run
      after item-16's degenerate-loop fix lands (so find_clamp-style tasks don't just time out).
      Only then make the final adopt/reject + decide whether to enable `codemode` by default in the
      global config. **Gated behind item 16.**

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
