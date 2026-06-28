# Item 28 — Lean feasibility probe (measured 2026-06-28)

A cheap, build-time feasibility probe run **before** committing to a formal-verifier
arm: **can the frozen local 4B (Gemma-4-E4B QAT, mlx-lm 0.31.3, served on
`127.0.0.1:8080`) write valid Lean, and can it use an externally-authored Lean spec to
fix Python?** This gates the two non-trivial regimes from the item-28 framework analysis:

- **Regime B (implement-in-Lean):** the 4B *writes* Lean (def + proof); Lean compiles it.
  Gated by Part A.
- **Regime C (Lean-as-precise-plan):** a capable model (here: the session agent, standing
  in for the Opus reflector) authors a Lean spec; the 4B *reads* it and fixes the bug in
  **Python**; the existing test verifies. Gated by Part B.

Toolchain: Lean 4.31.0 (elan via Homebrew `elan-init`). Verifier = `lean <file>` exit code
+ a `sorry`/`admit` cheat check. Sampling: greedy (temp 0.0). Probe script + raw outputs:
`scratchpad/lean_probe.py`, `lean_probe_results.json`.

## Part A — can the 4B WRITE compilable Lean?  → 3/6 sorry-free  (the proof gap)

| task | difficulty | naive | Lean-instructed |
|---|---|---|---|
| A1 `double` (a `def`, no proof) | trivial | **PASS** (14.4s) | **PASS** (9.4s) |
| A2 `n + 0 = n` (proof, closes by `rfl`) | trivial proof | **FAIL** (24.1s) | **FAIL** (42.1s) |
| A3 `0 + n = n` (proof, needs a tactic) | harder proof | request-fail (no content ×2) | **PASS** (19.4s, `by simp`) |

**Decisive reading — the 4B writes trivial `def`s but cannot reliably write PROOFS, which
is the part that matters** (the proof *is* the verification content):

- **A1 (def): 2/2.** `def double (n : Nat) : Nat := 2 * n` — the 4B handles non-proof Lean.
- **A2 (the EASIER proof): 0/2.** It **over-engineered a goal that closes by `rfl`**:
  naive narrated prose + hallucinated `import Mathlib.Data.Nat.Basic` (unavailable in a bare
  `lean` invocation) and never emitted a proof term; instructed wrote a full `induction …
  rw [Nat.add_succ]` that **fails** because `n + 0` reduces definitionally and `Nat.add_succ`
  doesn't apply. **Control: the correct one-liner `… := rfl` compiles in bare Lean (exit 0)**
  → the failure is the model's, not the environment's.
- **A3 (the HARDER proof): 1/2.** Passed only when instructed, and only because `by simp`
  happened to close `0 + n = n`. Naive returned **no usable content** twice (the model emits
  reasoning/empty on proof tasks).
- **Instructions (the "planner subagent needs Lean instructions" hypothesis) help but do not
  close the gap:** the primer flipped A3 → PASS and steered away from hallucinated Mathlib
  imports, but did **not** save A2 (still over-engineered a `rfl` goal).
- **Local-cost confound (itself a finding):** a bare `lean` has only Lean core, no Mathlib;
  the model repeatedly reaches for `import Mathlib…` and Mathlib `simp` lemmas. A
  representative Lean-proof environment needs **Mathlib = multi-GB download + a long build on
  the 16 GB M1** — exactly the local-runtime-cost constraint that argues *against* Lean here.

## Part B — does an externally-authored Lean spec help the 4B fix Python?  → 4/4 (non-discriminating)

| task | NL-spec | Lean-spec |
|---|---|---|
| B1 `factorial` (`n*factorial(n)` → `n*factorial(n-1)`) | **PASS** (21.9s) | **PASS** (38.6s) |
| B2 `clamp` (swapped lo/hi returns) | **PASS** (24.7s) | **PASS** (29.6s) |

- **The 4B consumes a Lean spec without being derailed** by the unfamiliar syntax — it still
  emits a correct Python fix → regime C is **mechanically feasible**.
- **But Lean did not beat NL, and was slower** (38.6 vs 21.9s; 29.6 vs 24.7s). These tasks are
  **too easy to discriminate** — the 4B fixes them from the buggy code alone, so the spec
  (NL or Lean) adds nothing measurable. A real test of regime C needs a **harder, discriminating
  task set** where the buggy code is genuinely ambiguous, and the cheaper **NL spec is the
  baseline the Lean spec must beat** to justify the extra latency + the Opus spec-authoring cost.

## Equivalent online-model probe — can a CAPABLE model (BigPickle) write Lean? (2026-06-28)

The user asked whether the Lean-proving architecture (regime B) becomes viable for a **capable
ONLINE** model where the 4B failed. Built the equivalent probe (`scratchpad/lean_probe_bigpickle.py`,
same tasks + same `lean` verifier, generation via `opencode run -m opencode/big-pickle`).

- **Capability bar: CLEARED (1 clean measured point).** On `addZeroEq` (`n + 0 = n`) — the task the
  **4B failed in BOTH conditions** — **BigPickle produced a valid, compiling, sorry-free *inductive*
  proof in 4.5 s** (`induction n with | zero => rfl | succ … calc … rw [ih]`; `lean` exit 0). So the
  capable online model **can** write real Lean proofs the weak 4B cannot.
- **Fuller run BLOCKED by gateway throttling (infra, not architecture).** The free opencode-zen
  gateway throttled BigPickle after the day's volume — A2/A3/A4 retries all hit the 85 s cap empty
  (early calls ran at ~4.5 s warm). Also hit opencode's non-TTY stdout **block-buffering /
  pipe-held-open deadlock** (a daemon child keeps the stdout pipe open → `subprocess.run` hangs;
  redirecting to a file buffers until clean exit, lost on kill). Re-runnable when the gateway is
  cool; the script is the deliverable.
- **Decisive point (from 28.1 research, capability-independent):** clearing the proof-writing bar
  does **NOT** make Lean a viable *verifier for Python*. AlphaProof/DeepSeek-Prover only work where a
  **formal statement pre-exists** (competition math); real Python bug-fixing supplies neither the
  per-instance spec nor the Python↔Lean autoformalization. **A stronger model removes neither wall.**
  ⇒ for the online tier the recommended verifier is an **AutoCodeSherpa-style PBT + symbolic-condition
  gate**, NOT Lean. BigPickle's proof ability is **necessary-not-sufficient**, and unused for Python.

## Verdict (feeds the item-28 framework recommendation)

1. **Regime B (4B writes Lean proofs) is NOT viable on this stack** — confirmed empirically,
   not just argued. The 4B writes trivial `def`s but cannot reliably write proofs; the proof is
   the verification, so Lean-as-output is out. Reinforces the **Tier-3 "avoid"** placement of
   Lean / proof assistants for the *verifier* role.
2. **Regime C (Opus writes the spec, 4B reads it, fixes in Python) is feasible but UNPROVEN
   to help** — the 4B isn't derailed by Lean, but on non-discriminating tasks Lean ≈ NL and
   slower. If pursued, it is a **planning-artifact** arm (Lean spec is never compiled against
   the candidate Python — that would reintroduce the Python↔Lean autoformalization gap), and it
   must be A/B'd **against a plain-NL spec** on a discriminating task set, which it has to beat.
3. **Net:** the probe does not change the headline recommendation — wire the verifier from the
   **spec-free Tier-1 signals (P2P-regression + `mypy` + `ruff`/parse)**, not Lean. Any Lean use
   is a **speculative regime-C plan-spec arm**, ranked below the Tier-1 gate and gated on beating
   an NL spec.
