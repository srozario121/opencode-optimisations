# Item 20.2 — feasibility smoke notes (arms b/c)

Progressive log (crash-safe; written after each rollout). Stack: frozen local
Gemma-4-E4B QAT / MLX / repair proxy ON (NO_THINK=0), 16 GB M1. Smoke instance =
`sympy__sympy-21614` (the T3 near-miss that EDITS CLEANLY at bare baseline, P2P 6/6
intact — so any failure to engage under a topology is attributable to the topology,
not the instance).

Feasibility question (per 20.2): *does Gemma emit **valid** tool-calls when driving
this topology?* A failed smoke is a **recorded wall-confirming null**, not an abort.

## Arm b — plan-arm-b-planbuild (procedural plan-then-build, append channel)

- **Sample 1** (`item20-smoke-b`, cap 360s): FAIL (no-edit), 101s, F2P 0/1.
  Episode parts: 2 step-start / 2 step-finish / **1 text / 0 tool**. The model
  emitted its search as a **markdown code block** —
  `[search]` + a ```json {pattern:"Derivative", include:"*.py"} block — i.e. an
  **INVALID, prose-formatted tool-call** opencode does not dispatch and the repair
  proxy does not catch (it only repairs malformed *structured* calls, not text).
  → On this sample the procedural "Work in two phases: 1. PLAN / 2. BUILD" append
  nudged the model to *describe* the tool call instead of *emitting* it. Echoes
  item 18 (terse/procedural instructions suppress tool use on this 4B) and research
  finding #1 (how-to/guideline plans hurt weak models). One rollout only — MLX is
  non-deterministic; second sample below.
- **Sample 2** (`item20-smoke-b2`, cap 240s): FAIL (timeout), 240.8s, F2P 0/1.
  **Stuck at step 0 the entire run** — episode jsonl EMPTY (never flushed a single
  completed step/part). **0 valid tool calls** again.
- **Arm b verdict (smoke, 2/2 samples): 0 valid tool calls.** On `21614` — which
  EDITS CLEANLY at bare baseline — the procedural "two-phase plan-then-build"
  append **suppresses tool emission** (S1 narrated an invalid prose-markdown call;
  S2 never produced a dispatched step). → **flagged as a likely wall-confirming
  null** for the feasibility precondition. NOT an abort (per 20.2): arm b still runs
  in 20.3 at K≥3, where this is confirmed or overturned against the cand2 base.

## Arm c — plan-arm-c-multiagent (orchestrator + planner/coder subagents via task tool)

- **Sample 1** (`item20-smoke-c`, cap 360s): FAIL (timeout), 360.4s, F2P 0/1.
  Episode parts: 9 step-start / **8 tool / 0 text**. The **8 tool calls are all
  VALID structured calls** (grep ×4, read ×4, every one `completed`) — so Gemma
  DOES emit valid tool-calls under this topology. **BUT it never invoked the `task`
  tool** to delegate to the `planner`/`coder` subagents — it ignored the
  orchestration nudge and fell into flat grep/read **tool-churn** (the 21627 mode),
  never committing an edit, then timed out at the 360s cap.
- **Arm c verdict (smoke): tool-calls VALID, but the multi-agent MECHANISM does not
  fire** — the weak model won't drive `task` delegation; it degrades to single-agent
  tool-churn. The feasibility precondition ("emits valid tool-calls") PASSES, but the
  intended topology is effectively inert. Full 360s/expensive even so (pre-figures
  research finding #6 cost concern). → arm c runs in 20.3 as the counter-arm; expect a
  net-loss / wall-confirming result, now with the mechanistic reason recorded.

## Smoke summary (for 20.3)

| arm | valid tool-calls? | topology mechanism fires? | dominant mode | note |
|---|---|---|---|---|
| b (plan-then-build) | **no** (0/0 over 2 samples) | n/a (single agent) | no-tool-stop / stuck | procedural append suppresses tool use; likely null |
| c (multi-agent/task) | **yes** (8/8 valid) | **no** (never calls `task`) | tool-churn → timeout | model won't orchestrate; degrades to flat churn |

Neither arm is aborted (20.2 rule). Both carry their caveat into the 20.3 K≥3 A/B.
Stack stayed healthy throughout both arms — no OOM during the smoke.

