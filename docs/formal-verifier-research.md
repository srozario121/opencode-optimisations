# Formal-verifier stage for a plan→verify→implement loop — deep-research survey (item 28.1)

**Status:** [lit-only] per the project Evidence policy — a *ranking*, not an adoption. Only a
local K≥3 harness A/B (28.3) may adopt or reject any verifier. Delivered 2026-06-28 (deep-research
workflow `wf_29a63d03-7d2`: 6 angles, 27 sources fetched, 119 claims extracted, 25 adversarially
verified → 24 confirmed / 1 killed). Pairs with the measured `docs/item28-lean-probe-notes.md`.

## Headline

The verifier-guided code-generation paradigm **works**, but **every demonstrated win comes from
EXECUTION / TEST / TYPE-based signals on FRONTIER-tier models** — not from heavyweight *formal*
verifiers (SMT / BMC / deductive provers) and **not, in any published result, on a weak ~4B local
model doing repo-level bug-fixing**. The binding constraint for real Python bug-fixing is the
**specification problem**: tasks ship no formal spec, and the formal tools that could consume one
need a hand-written contract and/or fully-typed code. So "formal verification" in the strict sense
is largely **unmet** here; the working signals are tests, types, and property-based/concolic checks.

## What the evidence says (by capability tier)

### Confirmed — verifier signals help, but mostly at the FRONTIER tier
- **Execution/test reranking & generated-test agreement** lift frontier models on single-function
  / competitive tasks: **LEVER** +4.6–10.9% (code-davinci-002), **CodeT** HumanEval pass@1 65.8%
  (+18.8pp), **AlphaCodium** GPT-4 CodeContests pass@5 19%→44%. *All frontier-of-era models;
  HumanEval/MBPP/CodeContests, NOT SWE-bench; signal is learned/execution, not formal.*
  (high; arXiv 2302.08468, 2207.10397, 2401.08500)
- **Strong critic disproportionately helps a weaker implementer** — the cleanest support for the
  plan→**verify**→implement intuition. Olausson et al. (ICLR 2024): self-repair gains are "sometimes
  not present at all" once repair compute is charged; the bottleneck is **diagnosis**, and swapping
  a *stronger* model's feedback into a weaker one raised passing repairs ~57%. *But the "weak"
  implementer is GPT-3.5 (frontier), not a 4B; the "verifier" is an NL critic + unit tests.*
  (high; arXiv 2306.09896) → this is the literature backing for "let a strong model (Opus/BigPickle)
  author the spec the local 4B implements against", **extrapolated** to the 4B tier.
- **Best directly on-point SWE-bench pattern = a lightweight AUTO-GENERATED executable verifier.**
  **AutoCodeSherpa** (SWE-bench Verified): a property-based test + program-internal symbolic
  conditions (executable, no hand-written formal spec) used as an accept/reject gate ~doubled
  correct rejection of bad patches and lifted Agentless plausible-patch rate 29.2%→47.0% (+60.7%
  rel). *Caveats: ran on gpt-5-mini (capable online), "plausible"≠correct (weak-oracle/overfit,
  53/140 false positives), single preprint, baseline-dependent "2×".* (medium; arXiv 2507.22414)

### The test suite itself is a WEAK, GAMEABLE oracle (sharpens item 28's anti-leakage rule)
- 19.78% of patches "solved" by top-30 SWE-bench agents are semantically incorrect; the top agent
  drops 78.8%→62.2% under strengthened tests (arXiv 2602/2603 family). 31% of "passed" patches are
  suspicious due to weak F2P tests (arXiv 2410.06992). Frontier models **reward-hack** test-based
  verification 76–93% of the time on impossible-task variants (ImpossibleBench). *Hardening the
  verifier (closing shortcut channels) cut "hacked-resolved" 28.6%→0.56% and raised clean-resolved
  40.2%→60.5%.* → the existing tests are the primary cheap oracle **but** must be hardened and the
  hidden F2P labels withheld from the in-loop verifier; a weak/leaky test signal is actively harmful.

### Formal-tool fit on untyped Python (the framework evaluation)
- **CrossHair — best technical fit among formal-methods-derived tools.** Z3/SMT-backed concolic
  symbolic execution that **runs the live function with symbolic-proxy objects, no AST/bytecode
  analysis, no static types** → can yield a cheap dense pass/fail on under-specified Python.
  *Caveat: it still needs SOMETHING to check (contract/assert/property or a differential reference);
  raw bug-fix tasks supply none, so a model or the test suite must provide it.* (high; CrossHair docs)
- **pyright > mypy as the type-consistency gate.** pyright type-checks ALL code and **infers return
  types from the body**; mypy by default **skips unannotated functions** and never infers returns.
  → pyright fires on untyped real-world code with no spec. *Type-consistency is a filter (catches
  type regressions), not a proof of logic correctness.* (high; pyright mypy-comparison doc)
- **Deductive Python verifiers (Nagini/Viper, and the Dafny/Frama-C/Why3 class) are out** — they
  require user-authored contracts (pre/post/invariants) AND full PEP484/mypy typing, and do not
  infer functional specs. A **spec-availability wall**, not merely a cost wall. (high; Nagini CAV
  2018)
- **Property-based testing (Hypothesis) + LLM-authored properties** is a viable spec-free
  *infrastructure* (Generator+Tester two-agent loops auto-derive properties from the NL issue), but
  the generated properties are fallible (the AutoCodeSherpa weak-oracle problem). (medium)

### Proof assistants (Lean / Coq / Isabelle) — NOT viable for either tier, and capability alone does NOT unlock them
- AlphaProof (Nature 2025): a Gemini autoformalizer + AlphaZero-RL proof net on ~80M auto-formalized
  problems — successes **confined to competition mathematics**, and **every problem arrives WITH a
  formal Lean statement** (exactly what under-specified Python lacks). DeepSeek-Prover (7B, ~8M
  synthetic Lean proofs) beats GPT-4 on miniF2F — **a small model CAN prove when heavily
  specialized, but only on hand-formalized math, zero Python transfer**. Baldur+Thor leave ~1/3 of
  *already-formalized* Isabelle theorems unproven. (high; Nature s41586-025-09833-y, arXiv 2405.14333,
  2303.04910)
- **Decisive for the online-model question:** the blocker to Lean-on-Python is **not** the model's
  proof-writing ability — it is (1) the missing per-instance formal spec and (2) the Python↔Lean
  autoformalization gap. **A more capable model removes neither.** So proof assistants do not become
  a viable *verifier for Python* just because the model is strong enough to write Lean.

## RANKED RECOMMENDATION

**(a) Local-4B on a 16 GB Mac** (item 28's local arms):
1. **Existing repo test suite (P2P regression subset)** — primary cheap dense oracle; **harden it +
   withhold the hidden F2P** (leakage + reward-hacking risk above).
2. **pyright type-consistency** — near-free always-on gate on untyped code.
3. **CrossHair (Z3-backed concolic) + Hypothesis property tests**, with the **properties authored by
   a STRONGER online model** (the weak 4B implements against a spec it does not have to invent).
- **AVOID** Nagini/Dafny-class deductive verifiers (need types + hand contracts) and **Lean/Coq**
  (no spec, needs proof-specialised model). → matches the user decision: **<16 GB local ⇒ no formal
  proving for now.**

**(b) Capable ONLINE model** (BigPickle-class — the online branch):
1. **AutoCodeSherpa-style auto-generated PBT + symbolic input/infection/output conditions** as an
   accept/reject gate (best demonstrated SWE-bench fit).
2. **Generated-test + execution-agreement** (CodeT/AlphaCodium pattern).
3. **CrossHair / Hypothesis + pyright** as supplementary differential/type signals.
- **Still no Lean/Coq** — not unlocked by capability (see above).

## Caveats (load-bearing)
- **Capability-tier gap is the biggest caveat:** ~all positive evidence is frontier/online
  (code-davinci-002, GPT-4, GPT-3.5, gpt-5-mini). There is **essentially no published evidence a
  verifier helps a weak ~4B local model on repo bug-fixing** — "verifiers disproportionately help
  weaker models" holds only at the GPT-3.5↔GPT-4 boundary. **This is exactly item 28's open
  empirical contribution.**
- **Task-scope gap:** most wins are single-function/competitive, not SWE-bench repo-level; only
  AutoCodeSherpa is genuinely SWE-bench Verified.
- **"Formal verifier" framing largely unmet:** no result uses a heavyweight SMT/BMC/deductive prover
  as the live signal on real Python; CrossHair is the closest SMT-backed tool that actually runs on
  untyped Python.
- **AutoCodeSherpa** (the most on-point) is a single self-reported preprint with a ~6× headline jump
  between versions and a known weak-oracle problem — indicative, not settled.

## Open questions this hands to 28.2/28.3
1. Does a verifier/critic signal actually move the needle for a 4B local model on repo bug-fixing, or
   does the weak generator's own low patch quality dominate (the self-repair feedback bottleneck)?
2. Can a strong online model reliably AUTHOR CrossHair contracts / Hypothesis properties that the 4B
   implements against — and how often do those auto-specs overfit/mis-specify?
3. Real local runtime/latency cost of CrossHair (Z3) + Hypothesis per candidate patch on a 16 GB Mac
   — cheap enough to gate the loop?
4. Do cheap signals combined (pyright + hardened P2P tests + CrossHair differential) approximate an
   AutoCodeSherpa-style gate **without** needing a capable model to write the spec?

## Primary sources
LEVER arXiv:2302.08468 · CodeT arXiv:2207.10397 · AlphaCodium arXiv:2401.08500 · Self-repair
(Olausson) arXiv:2306.09896 · AutoCodeSherpa arXiv:2507.22414 · CrossHair docs (pschanely/CrossHair)
· pyright mypy-comparison (microsoft/pyright) · Nagini EilersMüller CAV2018 · AlphaProof Nature
s41586-025-09833-y · DeepSeek-Prover arXiv:2405.14333 · Baldur arXiv:2303.04910 · SWT-Bench
arXiv:2406.12952 · ImpossibleBench / Verification-Horizon / weak-test SWE-bench audits (2410.06992,
2606.26300, 2603/2602 family) · VERIMAP arXiv:2510.17109 · PGS PBT two-agent (2506.18315).
