# Changelog — opencode-optimisations

Completed work, moved out of `TODO.md`. Newest milestones first within each
group. Items keep their original ledger numbers (1–15) for cross-reference with
claude-mem memory and the `docs/` research.

> **History note.** This repo was *extracted* (item 15) from a larger personal
> toolkit (`admin`), where the original 2,475-line TODO.md (items 1–16) lived.
> That ledger did not come across — items 1–16 were reconstructed from claude-mem
> cross-session memory so the numbering stays continuous. Items 1–15 are recorded
> here; the open work (item 16+) remains in `TODO.md`.

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
