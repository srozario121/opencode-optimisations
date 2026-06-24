# Planning-first / orchestration topology — research findings (TODO item 20.1)

> **Status: COMPLETE.** `deep-research` run `wf_48ab6f58-da0` (launched 2026-06-22,
> verification + synthesis completed on resume 2026-06-23 after an earlier
> session-limit interruption). 18 sources → 88 claims → 25 verified (18 confirmed,
> 7 refuted) → 8 synthesised findings. An earlier partial draft of this file marked
> several claims "unverified"; they have now been verified and are reflected below.

> **[lit-only] — NOT validated on this stack.** "Verified" below means *citation
> faithfulness* (3-vote adversarial claim-checking against the cited papers), **not**
> an experiment on Gemma-4-E4B / opencode / M1 — and no source tested that exact
> configuration. Per the repo Evidence policy these are **hypotheses**; both the
> positive claims (planning helps) and the negative claims (multi-agent is a net
> loss) must be measured on the local harness before any lever is adopted or
> rejected. The local validation is **TODO item 20.3** (a multi-arm A/B incl. a
> multi-agent counter-arm), gated behind item 16.

The question: for a **weak local coding model** (Gemma-4-E4B class, ~8–12 tok/s,
16 GB M1, fragile tool-calls, prone to **degenerate decoding loops**), is it worth
making the **main loop a pure orchestrator** and running a **dedicated plan
sub-agent before any build sub-agent**? Model + engine FIXED; only orchestration
is tunable.

---

## Headline (verified)

**A full orchestrator-only main loop with a chain of sub-agents is most likely a
net loss at 8–12 tok/s. A *constrained plan-then-build separation* is the part
worth prototyping — but only as a SHORT GOAL-STYLE plan feeding a thin executor.**

The evidence cuts both ways and the recommendation threads them:

- **For (planning):** an explicit up-front planning pass reliably lifts code-gen
  correctness (**+25.4% Pass@1 vs direct, +11.9% vs CoT**); even a **single
  lookahead step provably dominates** flat greedy/ReAct; and for small models,
  **planner-only thinking helps** while **tool use beats explicit thinking**.
- **Against (heavyweight orchestration):** multi-agent costs **1–2 orders of
  magnitude more tokens** (15× Anthropic, 10–100× patching, 4–220× UIUC); it does
  **not consistently beat a well-designed single agent** on coding (a single
  general agent beat all patch-specific multi-agent systems **16/19**; one model
  *dropped* 13/19→8/19 going multi-agent); and a central orchestrator is a **single
  point of error propagation** — dangerous when the hub is itself a fragile model.

**Recommendation:** prototype a **minimal 2-role shape** — *one planning pass that
emits a short goal-style plan, then a flat ReAct executor that does all tool work*
— **not** a pure-orchestrator hub spawning workers. This captures the plan-then-act
benefit and narrows the executor's decision surface while avoiding the token tax
and the orchestrator-cascade risk.

---

## The three findings that change the design

1. **Plan TYPE must match model capacity — goal-style, not guideline-style.**
   A weak model fed *detailed how-to* plans can do **worse than no plan**; *goal*
   (what-to-achieve) plans help. Llama-1B self-planning: **None 25.2% → Guideline
   23.2% (worse!) → Goal 30.2%**. (arXiv:2506.11578, 3-0.) → the plan pass must emit
   a **short goal statement**, not a step-by-step procedure.

2. **Unrestricted "full thinking" induces the exact item-16 pathology.** On a 4B
   model, full thinking **collapsed** Level-2 accuracy 16.28% → 3.49% via
   "controller instability (**tool-call loops ending in `<tool_call>`**),
   non-termination, and output-contract drift" — while **planner-only thinking
   helped** and **tool use beat explicit thinking** (4B+tools 18.18% > 32B no-tools
   12.73%). (arXiv:2601.11327, 3-0.) → keep the *executor* thin; concentrate
   reasoning in the bounded plan pass.

3. **The planning benefit is a within-policy lookahead mechanism, not a sub-agent.**
   A single step of lookahead strictly dominates step-wise greedy (Prop 3.3), but
   the proven mechanism is *forward simulation inside one policy*, **not** a separate
   planning agent. (arXiv:2601.22311, 3-0.) → you likely don't need a second
   *agent* at all; an inline pre-plan in one pass may suffice — and is cheapest.

---

## Verified findings table

| # | Finding | Vote | Source |
|---|---|---|---|
| 1 | Up-front planning improves code-gen: +25.4% Pass@1 vs direct, +11.9% vs CoT — but shown on LARGE models; self-planning is called an "emergent large-model ability", gated on plan quality | 3-0 / 2-1 | arXiv:2303.06689 |
| 2 | Small models: planner-only thinking helps; **full thinking hurts** (4B 16.28→3.49%) causing `<tool_call>` loops / non-termination; tool use > thinking | 3-0 / 2-1 | arXiv:2601.11327 |
| 3 | Stronger-planner/weaker-executor lifts weak executors (+10pp MATH-500); **plan type must match capacity** (goal > guideline > none for weak models) | 3-0 | arXiv:2506.11578 |
| 4 | A single lookahead step strictly dominates flat greedy/ReAct; mechanism is within-policy forward simulation, **not** a separate planning sub-agent | 3-0 | arXiv:2601.22311 |
| 5 | Plan-and-execute architectures (ReWOO Planner/Worker/Solver, Plan-and-Solve) formally decouple planning from execution and cut missing-step errors | 3-0 | arXiv:2305.18323, 2305.04091 |
| 6 | Multi-agent costs **1–2 orders of magnitude** more tokens (15× / 10–100× / 4–220×) — punishing at 8–12 tok/s | 3-0 | Anthropic; arXiv:2603.01257 |
| 7 | Multi-agent does **not** consistently beat a well-designed single agent on coding/patching (single general agent 16/19; one model dropped 13/19→8/19) | 3-0 / 2-1 | arXiv:2603.01257 |
| 8 | Central orchestrator = single point of failure for error propagation (LangGraph hub injection → 100% failure vs 9.7% at a leaf); reviewer/QA roles don't reliably stop cascades | 2-1 / 3-0 | arXiv:2603.04474 |

## Refuted (do not rely on)

| Claim | Vote |
|---|---|
| Anthropic orchestrator-worker beat single-agent by **90.2%** | 1-2 |
| Multi-agent is *explicitly unfit* for coding | 0-3 |
| Plan-then-execute beats CoT *across all datasets by a large margin* | 1-2 |
| **Planning helps weak models MORE in relative terms** / scale-independent (FLARE) | **0-3** |
| Multi-agent overhead is driven by iteration *depth* not agent count | 1-2 |
| Self-generated guideline plans *always* hurt weak models (the plan-TYPE data holds; the absolute claim does not) | 1-2 |

---

## Implications for this stack

- **Do NOT build the pure orchestrator + plan-subagent + build-subagent topology**
  for the local model. Cost (8–15×+), inconsistent coding benefit, and
  orchestrator-cascade risk all argue against it at 8–12 tok/s.
- **Prototype the minimal 2-role shape** (item 20.2): one bounded **goal-style**
  planning pass → a **thin flat ReAct executor**. Candidate implementations, cheapest
  first:
  1. **Single-pass constrained template** — emit a short goal plan *then* the first
     tool call in **one** rollout (no second agent; matches finding #4's
     within-policy mechanism; zero extra rollouts). *Likely the best cost/benefit.*
  2. opencode's native **`Plan` primary → `Build` primary** (no `task` tool).
  3. A true separate planning sub-agent (most expensive; only if 1–2 underperform).
- **Plan content rule:** goal/what-to-achieve, **not** detailed how-to (finding #1, #3).
- **Keep the executor thin** — minimal toolset, minimal thinking (finding #2). This
  *aligns* with item-11's "drop `task`/shrink decision surface", resolving the tension.
- **Gate behind item 16 (item 20.3):** the same literature shows *unbounded* thinking
  triggers the `<tool_call>` loop, so a planning phase could *worsen* the item-16
  pathology if unbounded. Measure on item-16 E0 metrics (degenerate-loop rate,
  fraction-of-budget-to-first-tool-call); **adopt only if it lowers the loop rate.**

## Open questions (carried into 20.2/20.3)

- Does the GAIA-derived "planner-only helps / full-thinking hurts" result transfer
  to a **pure SWE-bench coding** harness on Gemma-4-E4B, or do longer tool chains /
  file edits flip it?
- Does an up-front plan pass **reduce or worsen** the "repeat the plan sentence then
  never act" loop? No source measures this directly — needs an A/B with **loop-rate
  as the primary metric**.
- Is a **flat executor + single inline pre-plan** strictly better than any 2-agent
  split for a fragile local model (the cheapest-mechanism question)?

## Caveats

No source measures the exact configuration (a weak local model as both orchestrator
and planner in a coding harness). The two pillars rest on cross-domain
extrapolation: the coding-correctness planning gains are from **large** models
(self-planning is called an emergent large-model ability), and the small-model
planner-only / `<tool_call>`-loop evidence is from **GAIA** (general assistant), not
pure SWE-bench. The degenerate-loop pathology itself is not directly studied — the
nearest proxy is 2601.11327's "controller instability / non-termination". Most
strong sources are 2026 preprints (only 2303.06689 / 2305.04091 peer-reviewed).
**Net: the *direction* is well-supported and internally consistent; the *magnitude*
on this exact stack must be measured by prototype, not assumed.**

## Sources

- arXiv:2303.06689 — Self-planning code generation (peer-reviewed, TOSEM 2024).
- arXiv:2601.11327 — small-model agentic thinking-vs-tools ablations (GAIA).
- arXiv:2506.11578 — planner/executor capacity matching; plan-type ablation; COPE.
- arXiv:2601.22311 — lookahead strictly dominates greedy (Prop 3.3); FLARE.
- arXiv:2305.18323 (ReWOO), arXiv:2305.04091 (Plan-and-Solve) — plan-execute decoupling.
- Anthropic multi-agent research system; arXiv:2603.01257 — multi-agent cost + single-vs-multi patching.
- arXiv:2603.04474 — multi-agent error propagation / orchestrator-as-cut-set.
