# Tiered validation harness (TODO item 17)

**Why this exists.** Through item 16 the harness was effectively *binary*: the
synthetic micro-suite (item 14) saturated near the top while the full SWE-bench
subset (item 11) sat at a flat **0/8**, with nothing in between. A flat zero gives
no *gradient* — you can't tell whether a lever helped a little, nor *which* task
class or failure mode a change moved. Item 16's standing note made the diagnosis
explicit: the 8 sympy instances are *all* hard (multi-file / multi-site real
fixes), so a weak model (Gemma-4-E4B) can never register a non-zero signal, and
without one no lever (or GEPA, item 19) has anything to optimise against.

Item 17 fixes that by laying **one difficulty ladder over both existing
harnesses** (decision: *unify*, not build a third) and replacing the single
pass/fail number with a **per-tier × per-failure-mode** breakdown.

## The four-tier ladder

The ladder spans both harnesses. The synthetic micro-suite supplies the easy,
*passable* rungs a weak model can clear; the real SWE-bench fixes supply the hard
rungs. Source of truth in code: `GLOBAL_TIERS` / `MICRO_TIER_MAP` in
`scripts/harness_eval.py`.

| Tier | What it tests | Source | Pass = |
|---|---|---|---|
| **T1** | single tool-call fidelity (right tool, well-formed, right args) | micro-suite, local tier 1 | all checks green |
| **T2** | multi-step tool sequence + a synthetic one-line micro-edit | micro-suite, local tiers 2 + 3 | all checks green |
| **T3** | single-file, localized **real** bug-fix | SWE-bench: 1 file · 1 hunk · 1 F2P | F2P flips, P2P intact |
| **T4** | multi-file / multi-site real bug-fix needing reasoning | SWE-bench: any multi- (file/hunk/F2P) | F2P flips, P2P intact |

**Micro → global map.** The micro-suite keeps its own local tiers (1/2/3); the
report maps them through `MICRO_TIER_MAP = {1:1, 2:2, 3:2}`. T1/T2 are therefore
*always* synthetic; T3/T4 are *always* real SWE-bench fixes. A "tier pass" is
uniform across suites — `classify_failure(...) == "ok"` (a real F2P flip, or all
micro checks green).

### How SWE-bench instances are bucketed (T3 vs T4)

Assigned **offline and reproducibly** from each instance's cached gold patch +
its FAIL_TO_PASS set by `harness_eval.py tier` (which calls `assign_tier`):

- `n_files` = non-test source files the gold patch edits
- `hunks`   = number of `@@` change blocks (edit sites)
- `needs_search` = `n_files > 1` **or** `hunks > 1` (the fix spans >1 site → must
  locate each)
- **T3** = the easiest real fixes: **one** file, **one** hunk, **one** F2P test
- **T4** = anything multi- (file / hunk / F2P) → more reasoning / coordination

The frozen subset (`scripts/harness_eval_subset.json`) carries the result on each
instance (`tier`, `n_files`, `needs_search`, `needs_bash`, `expected_tool_seq`).
For the current 8-instance sympy subset this is **T3 = 3** (21614, 12481, 21627)
and **T4 = 5**. Re-derive any time with `harness_eval.py tier` — it is idempotent
and preserves the subset's `frozen_at`.

## The shared failure taxonomy (`failure_category`)

One vocabulary across both suites and items 16/17/18 (`FAILURE_CATEGORIES` in
`harness_eval.py`). The category is **derived from the observed run** — the
terminal `reason` plus the E0 metric block — *not* a static tag (decision B). The
first seven are item-16's defect modes (what the harness/tool levers and the
item-18 trace recommender target); the last three are non-defect terminal
outcomes, kept so the histogram sums to *n* and a *wrong-but-clean* fix is not
mislabelled a harness defect.

| category | meaning | derived from |
|---|---|---|
| `oom` | server crashed mid-episode (Metal OOM) | `reason == "oom"` |
| `degenerate-loop` | stuck repeating a planning sentence / ran to the cap | E0 `degenerate_loop` |
| `timeout` | hit the wall-clock cap, no degenerate signature | `reason == "timeout"` |
| `no-edit` | spent the turn, produced no patch (incl. dropped-output) | `reason == "no-edit"` |
| `edit-mismatch` | an edit/patch call failed to apply | `reason == "apply-failed"` or an errored edit tool |
| `grep-parse-error` | a search call errored, no edit landed | E0 `errored_tools` ∋ grep/glob |
| `catastrophic-edit` | edited but **regressed** a previously-passing test | `tests-failed` ∧ P2P < total |
| `tests-failed` | edited cleanly but the fix is wrong | `tests-failed` ∧ P2P intact |
| `error` | harness-level exception scoring the instance | `reason` startswith `error` |
| `ok` | passed (not a failure; present so the histogram totals *n*) | `passed` |

Precedence is the table order (most specific / most severe first): e.g. a
degenerate loop that also times out classifies as `degenerate-loop`, and a
`tests-failed` that broke a P2P test is the more serious `catastrophic-edit`.

Micro tests have no real test flip, so they only contribute `ok` / `timeout` /
`oom` / `error`, plus a partial-check miss mapped to `edit-mismatch` (micro edit
tier) or `no-edit` (a call/sequence tier — the asked-for call never landed).

## The report (17.4 / 17.5)

Both harnesses already share one JSONL ledger
(`~/.config/opencode-optimisations/harness-eval/ledger.jsonl`), so the tiered view
is built at the **reporting layer** — no merge of the two run engines.

- **Rendered table** (`_render_tier_report`, folded into `summary.md`): per config,
  the per-tier pass/total cells (T1–T4), a Δ vs that suite's baseline, and the top
  derived failure modes. Historical rows written before tiers were recorded fall
  back to the manifest by `instance_id`, so the split is retroactive.
- **Structured artifact** (`build_tier_report` → `tier-report.jsonl`, one record
  per config): per-tier `pass` / `total` / `pass_rate` / `delta_vs_baseline` /
  `failure_histogram`. This is the machine-readable surface and is intentionally
  **cheap — pure aggregation over the ledger** — so item 19's GEPA loop can read it
  as a fitness signal without re-running anything.

### Used as the GEPA fitness signal (item 19)

Item 19's optimiser reads exactly this cheap aggregation as its fitness function
(`gepa_tier_cell` / `gepa_krun_stats` over the ledger, no re-run). The scalar is
**`score = T2_frac − λ·(rise above baseline in no-edit + error + catastrophic-edit)`**
with **λ large** (any tool-call-floor regression drives the score negative — a T2 gain
can never buy it back) and a **T1 hard gate** (any T1 drop ⇒ rejected outright). **T2 is
the only climbable rung**; T3/T4 are reported but weight 0 (the stable 0/8 capability
wall has no gradient). The unlock rule that decides whether GEPA may run at all is
`(ceiling − T2_mean) > K-run spread` (`gepa_gate_check`). Item 19 used this to ADOPT a
terse-rules candidate (T2 0.733→0.917); see `docs/structured-optimisation-research.md`
§19.2–19.3 and `harness_eval.py gepa-gate` / `gepa-score`.

### Commands

```bash
# (offline) assign/refresh tiers + metadata on the frozen subset
python scripts/harness_eval.py tier

# render + persist the tiered report (also runs inside `summary` and every `run`)
python scripts/harness_eval.py report

# (item 18) Layer-1 evidence digest over the on-disk episode/ledger corpus
python scripts/harness_eval.py recommend

# offline sanity (no model): classifier branches, tier buckets, report render
python scripts/harness_eval.py selftest
```

A normal `run` (either harness) appends to the ledger and regenerates both
`summary.md` and `tier-report.jsonl`, so the gradient stays current with no extra
step.

## The recommender consumes this report (item 18)

Item 18's improvement-recommender reads exactly these artifacts — the per-episode
`opencode.jsonl` (E0 metrics via `parse_episode_jsonl`), the `ledger.jsonl` rows,
and `tier-report.jsonl` — and aggregates them by `failure_category × tier` into an
**evidence digest** (`harness_eval.py recommend`). The digest reuses this doc's two
primitives unchanged: `classify_failure` for the mode vocabulary and `instance_tier`
for the ladder, so it speaks item-17's language with no new enum. Its `ranked_cells`
order cells by `count × headroom × movable` — and because **T3/T4 carry
`movable: false`** (the stable 0/8 capability wall), the priority hint zeroes them
out and surfaces only the T1/T2 rungs item 19 can actually climb. An Opus-4.8
proposer then turns that digest into ranked item-17 lever configs (run via
`harness_eval.py run`), closing the loop back onto this same report. See
`docs/opencode-local.md` (item 18) for the full two-layer design.
