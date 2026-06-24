# Sandboxed code-execution for parallel/chained tool calls — research survey

**Item:** TODO 21.1 · **Run:** `wf_42940d55-80e` (deep-research, 18 sources, 85 claims
extracted → 25 verified → 19 confirmed / 6 refuted → 9 synthesized) · **Date:** 2026-06-23

> 🛠 **To turn this on**, see the setup guide: [`docs/codemode-setup.md`](./codemode-setup.md).

> **[lit-only]** per the repo Evidence policy. Every quantitative claim below is
> citation-checked against the source, **not** measured on this stack. The decisive
> number — whether Gemma-4-E4B can emit correct orchestration code — is **unmeasured**.
> 21.3 is its local validation.

---

## TL;DR verdict

> **⚑ UPDATE 2026-06-23 — the make-or-break gate was MEASURED and PASSED.** The survey's
> central negative (below) was `[lit-only]`; a local probe now shows **Gemma-4-E4B emits
> correct orchestration code at pass@1 1.0 (18/18), identical to the frontier control** on
> 6 simple/moderate chained-tool tasks. **Size is not the blocker at this tier.** See the
> *Empirical addendum* at the bottom — the verdict below is the pre-measurement literature
> view, kept for the record; the prototype is now **green-lit** (with complexity/Monty-dialect
> caveats), not high-risk-dropped.

The **mechanism is sound and "Monty" is technically deployable offline** on the frozen
stack. The *literature* offered **no positive evidence a ~4B model can reliably write the
orchestration code** and substantial evidence against — **but that prediction has now been
locally refuted for simple/moderate chains** (see addendum). Per the Evidence policy, the
local measurement supersedes the literature ranking.

- ✅ **Round-trip elimination is real and near-tautological** for control flow: loops,
  conditionals, error handling, and intermediate state run *inside* the emitted code,
  making **zero** model calls; large tool results are filtered in-sandbox so context
  sees only the consolidated result. This is exactly the lever the hypothesis targets.
- ✅ **Monty fits the stack perfectly on infra grounds** — in-process Rust VM, ~5 MB,
  microsecond start, zero-access-by-default, MIT, fully offline. Lowest-overhead option
  by far vs Pyodide / workerd / E2B / Firecracker.
- ❌ **The make-or-break weak-model question fails on current evidence.** Every
  quantified win is from **frontier** models (Sonnet 4.5, GPT-4-class). Small models hit
  a documented **"structure tax"**; the most direct source recommends structured
  code-agents only for **"32B+ or frontier models."** Multiple pro-weak-model claims were
  **actively refuted** in verification (0-3 / 1-2).
- ⚠ **Monty itself is alpha** (v0.0.18, Python *subset*, codemode integration not yet
  shipped) — clashes with the "frozen stack" requirement. The **pattern** transfers; the
  specific package is a moving target.

**Recommendation:** do NOT adopt on the literature. Run the cheap decisive experiment
first (open question #1 below). If Gemma-4-E4B can't emit a correct 2–5-tool code block
at an acceptable rate, drop the item. If it can, the slow 8–12 tok/s decode means the
round-trip savings are proportionally *larger* here than in any cited benchmark.

---

## Q1 — What is "Monty"? (high confidence, 3-0)

Unambiguously **Pydantic Monty**: *"A minimal, secure Python interpreter written in Rust
for use by AI."* A **from-scratch bytecode VM** using Ruff's parser — **not**
CPython-with-restrictions, **not** Python-on-WASM. Host tools are exposed to the model as
ordinary Python functions via a developer-controlled `external_functions` dict; security
is **zero-access-by-default** (no filesystem, network, or env vars) with opt-in
capabilities.

- **Footprint / offline (3-0):** embedded in the parent process; **~4.5 µs** start (no
  type check), 4.8 ms with; **~4.5 MB** binary, **~5 MB** added to the CPython process.
  Compare Docker 195 ms, cloud sandbox ~1000 ms+. Runs **fully offline** — trivially
  feasible on a 16 GB M1.
- **Intent (3-0):** *"Monty will (soon) be used to implement codemode in Pydantic AI"* —
  the same pattern Cloudflare calls CodeMode. Exposes registered tools as in-code
  functions, enabling loops / conditionals / `asyncio.gather` parallelism in **one turn**.
  Integration is **in-progress** (pydantic-ai PR #4153 `CodeExecutionToolset`), **not
  shipped**.
- **Maturity caveat (3-0):** **experimental, NOT production-ready** (v0.0.18, 2026-05-29,
  PyPI "Alpha"). Implements only a **Python subset** (no classes/generators/most stdlib;
  `match` "coming soon") and still solicits security-vuln reports. The subset matters: a
  weak model must emit code in an **unfamiliar restricted dialect**, compounding code-gen
  risk (its training distribution is standard Python, not Monty's).

Sources: `github.com/pydantic/monty`, `pydantic.dev/articles/pydantic-monty`,
`simonwillison.net/2026/Feb/6/pydantic-monty/`, `pypi.org/project/pydantic-monty/`.

## Q2 — Monty vs alternatives (high confidence)

| Option | Runtime | Start / overhead | Offline on M1? | Notes |
|---|---|---|---|---|
| **Monty** | in-process Rust VM, Python subset | **~µs**, ~5 MB | **Yes** | Lowest overhead; alpha; restricted dialect |
| Pyodide | Python→WASM | **~2 s** init (1 s net + 1 s cpu) | Partial | 5–6 orders slower start than Monty |
| Cloudflare Code Mode / workerd | JS/TS, V8 isolate | <1 s cold (snapshot) | **No** | Worker Loader **closed beta**, cloud-only |
| Anthropic code-execution-with-MCP | pattern (their infra) | n/a | **No** | Pattern transfers, infra doesn't |
| E2B / Firecracker microVM | full VM | VM-boot, cloud-oriented | **No** | Heavy; not in-process |
| Native bash in agent | shell | zero | Yes | No isolation, no tool-as-function exposure |

**For an offline, single-user, latency-sensitive M1 loop, in-process Monty dominates on
startup/footprint.** Cloudflare Code Mode is out (cloud/JS, closed beta); only its
*pattern* transfers. (Pyodide figure 3-0; rest synthesized from footprint comparisons.)

## Q3 — Does the pattern actually reduce round-trips / tokens / wall-clock? (high)

**Mechanism: yes, strongly supported (3-0, merged from 5 claims).** Anthropic: *"Loops,
conditionals, and error handling can be done with familiar code patterns rather than
chaining individual tool calls"*; *"agents can filter and transform results in code before
returning them"* (agent sees **5 rows instead of 10,000**). Cloudflare: *"the output of
each tool call must feed into the LLM's neural network… wasting time, energy, and
tokens… it can skip all that, and only read back the final results."* Pydantic: incident
triage of *"six sequential round trips"* collapsed to *"a single exec call."*

**But the magnitude is modest in the published examples, and frontier-sourced (3-0):**

- Weather agent: standard tool-calling = **4 sequential round-trips**; CodeMode = **2 LLM
  calls / 1 script** — but **12.2 s → 9.1 s**, **$0.019 → $0.017** (only $0.002 saved).
  Source concedes *"the saving is relatively modest because the example is simple"* —
  and it ran on **Claude Sonnet 4.5**.
- Cloudflare's headline **99.9%** (≈1,000 vs 1.17M tokens) and Anthropic's **98.7%** are
  **static API-exposure / tool-definition footprint, NOT end-to-end task savings.** The
  1.17M is hypothetical ("would consume") — a count of 2,500+ endpoint definitions.
- **Real measured end-to-end savings: ~32% (simple) to ~81% (complex batch)** (WorkOS).
- Anthropic's specific **150k→2k** progressive-disclosure figure was **REFUTED (0-3)** —
  do not cite it.

**Stack-specific upside:** weak models that can't parallelize incur *more* standard
round-trips (the source notes "one function call at a time… 7 round trips"), and at
**8–12 tok/s** each round-trip is far costlier than on cloud inference — so this stack
could gain **proportionally more** wall-clock *if and only if* the model writes correct
code.

## Q4 — Does it work on a weak ≤7B model? (MAKE-OR-BREAK — high confidence, evidence AGAINST)

**No positive evidence; substantial evidence against (3-0).**

- **"Structure tax"** (HF `structured-codeagent`): *"smaller models struggle to
  simultaneously handle JSON formatting, Python syntax, and the actual problem-solving
  logic"*; recommends structured code-agents only for **"32B+ parameters or frontier
  models"**; uses Mistral-7B as the concrete negative.
- **SLM code-gen failure band** (arxiv 2507.03160v4): PolyCoder (0.4B/2.7B) and Incoder
  (1.3B/6.7B) score **pass@1 below 0.10**; *"parameter count alone does not fully
  determine performance."*
- **CodeAct** (ICML 2024): the headline *"up to 20%"* is **gpt-4-1106 best case**; small
  open models scored single-to-low-double digits (**Mistral-7B 0%** on M3ToolEval) — the
  win is unreachable at ≤7B.
- **Anthropic's article assumes competence** (*"Models are great at navigating
  filesystems"*) and gives **zero** analysis of minimum model size.
- **Verifiers actively rejected pro-weak-model evidence:** "CodeAct works on 7B" (0-3),
  "guided decoding lets SLMs match large models" (0-3), "sub-10B strong performers" (1-2).

**One nuance that cuts slightly FOR the stack:** the structure tax specifically condemns
**JSON-wrapped** code; the source notes small models *"would otherwise perform reasonably
well with simpler markdown-based code generation."* A **plain markdown code block** (closer
to how Monty would be driven) is **less penalized** than JSON-enveloped codegen. This is
the one thread worth pulling in a prototype.

## Q5 — New failure modes

- **Code-gen errors** — the dominant risk for a weak model (Q4). A malformed single script
  fails the *whole* batch, vs one retryable tool call. Repair-loop cost can exceed the
  round-trips saved.
- **Restricted-dialect mismatch** — Monty's Python subset is off-distribution for the model.
- **Harder debugging / partial-failure handling** — a chained script that fails at call 4
  of 5 is more opaque than a per-call ReAct trace; no benchmark quantifies this for weak
  models.
- **Security surface** — code execution broadens attack surface; Monty mitigates with
  zero-access default + opt-in capabilities, but it's **alpha** and still soliciting
  vuln reports.

## Q6 — Offline feasibility on the frozen stack

**Infra: yes — Monty runs fully offline, in-process, ~5 MB, on a 16 GB M1, opencode-side
only, no cloud.** It is the *only* fully-offline in-process option (workerd/E2B/Firecracker
are cloud; Pyodide is heavier and slower). **Caveats:** (a) Monty is **alpha** and its
codemode integration is **unshipped** — depending on a moving alpha clashes with "frozen
stack"; (b) opencode is TS/JS while Monty is a Python/Rust package — wiring it in is a
non-trivial integration, not a drop-in. The lower-friction first cut may be a custom
opencode tool that shells a constrained code block to a local Python+Monty (or even a
plain restricted `exec`) subprocess.

---

## Caveats (from the run)

- **Vendor-blog weighting.** Much of the pro-case rests on first-party engineering blogs
  (Anthropic/Cloudflare/Pydantic) promoting their own products. Mechanism descriptions are
  accurate and mutually corroborating; **quantitative wins are self-benchmarks on fast
  cloud hardware with frontier models**, and two were walked back in verification.
- **No wall-clock data on sub-12-tok/s local models** exists in any source.
- **The decisive gap:** zero direct positive evidence for a ~4B model at orchestration
  code-gen; closest evidence is negative. Only mitigation: markdown < JSON penalty.

## Open questions → feed 21.2 / 21.3

1. **(decisive, do first)** Actual pass@1 of Gemma-4-E4B QAT on mlx-lm when asked to emit
   **one** Python code block calling 2–5 host tools with a loop/conditional. Measure
   before committing anything.
2. Does Monty's **restricted subset** raise or lower code-gen success vs full Python for a
   weak model (training distribution is standard Python)?
3. On 8–12 tok/s, what's the **break-even chain length** where one-code-block + repair-loop
   beats sequential ReAct, accounting for frequent weak-model code-gen failures?
4. Can **constrained/guided decoding** or a **fixed few-shot orchestration skeleton**
   (fill-in-parameters only) lift a ~4B model into reliable emission? (The broad
   "guided decoding matches large models" claim was refuted — but untested on *this* stack.)

## Refuted claims (do not cite)

- Anthropic 150k→2k / 98.7% progressive-disclosure figure (0-3).
- "LLMs are better at code than tool-calls because they saw more code in training" (0-3).
- "CodeAct demonstrated on 7B models" (0-3).
- "SLMs with guided decoding match large models on tool use" (0-3).
- Best sub-10B coders reach 0.59–0.67 pass@1 (1-2, killed).

## Empirical addendum — local pass@1 MEASURED (2026-06-23, item 21.2 probe)

> This section is **not [lit-only]** — it is a local-harness measurement on the frozen
> stack, and it **refutes the survey's central negative prediction** for this task tier.

**Probe:** `scripts/codegen_probe.py` — 6 frozen orchestration tasks (chain 2–5 mock host
tools with loops/conditionals, emit ONE markdown code block, assign `result`). Grading is
execution-against-mocks: a sample passes only if it emits a clean fence, parses, **calls
the required tools at runtime**, uses real control flow, runs, and returns the
independently-computed correct value (hardcoding is rejected — fixture values never appear
in the prompt). Markdown code block, **not** JSON-wrapped (the survey's one pro-small-model
nuance). Executor = restricted in-process `exec` (the survey's recommended primary; Monty
hook stubbed). k=3 samples/task.

| Target | Model | Transport | pass@1 | Samples |
|---|---|---|---|---|
| **local-gemma** | Gemma-4-E4B QAT (MLX :8080) | http | **1.0** | 18/18 |
| **bigpickle** | `opencode/big-pickle` (frontier control) | opencode | **1.0** | 18/18 |

**Per-task:** all six (`sum_lines`, `find_todo`, `big_balance`, `orders_over_10`,
`debug_plugins`, `count_big_files`) = pass@1 1.0 on BOTH models; zero failures at any
stage (format / parse / exec / tools / orchestration / correct).

**Size-isolation readout: Δ = 0.0 → model size is NOT the blocker at this task tier.**
The local 4B emitted correct chained-tool orchestration code as reliably as a frontier
model. This **flips the survey's make-or-break finding** ("no positive evidence a ~4B model
can write orchestration code; substantial evidence against") — for simple-to-moderate
chains, the evidence on THIS stack is now positive. The "structure tax" did not bite here,
consistent with the nuance that **markdown** code (not JSON-wrapped) is far less penalized.

**Honest limits of this result (why it's a GREEN-LIGHT-TO-PROTOTYPE, not a closed win):**
1. **Tasks are simple/moderate** — ≤7 tool calls, single-level loops/conditionals, ≤6
   tools, no parallelism (`asyncio.gather`), no nested control flow, no cross-call error
   handling. The structure tax may still appear at higher orchestration complexity.
2. **k=3 is small** — 18/18 is a strong provisional pass but can't distinguish a true
   rate of 1.0 from ~0.9; raise k before treating 1.0 as exact.
3. **Restricted `exec`, not Monty** — Monty's Python *subset* (off-distribution dialect)
   is untested and could lower success; the dialect-mismatch risk remains open.
4. **Clean controlled prompt** — explicit signatures + "assign to `result`". Real opencode
   usage is messier; the contaminated-control caveat applies to the big-pickle arm too
   (it inherited opencode's MLX-oriented system prompt).

**Revised verdict:** the make-or-break gate is **provisionally PASSED**. The risk has
*moved* from "can the weak model write code at all" (answered: yes) to "does it hold at
**higher orchestration complexity** and under **Monty's restricted dialect**." Next step is
to harden the probe (harder tasks: parallelism, nesting, error handling, larger tool
surface; higher k) before building the sandbox integration — not to drop the item.

## Empirical addendum 2 — HARDER tier + real Monty VM (2026-06-23, item 21.2b)

> Also **not [lit-only]** — measured on the frozen stack. Extends addendum 1 along the two
> axes it flagged as open: **task complexity** and **Monty's restricted dialect**.

**Probe extension:** `scripts/codegen_probe.py` gained a **hard tier** (5 tasks: nested
loops, cross-call **error handling** via `try/except`-skip, argmax transform, filter chain,
sort/select) exposing the **full 13-tool menu** (incl. distractors) so the model must
*select* tools. The real **`pydantic_monty` v0.0.18** engine was wired (`external_functions`,
`max_duration_secs` bound, `result` = last expression). `--engines exec monty` grades the
**same** generated output through both VMs (apples-to-apples). k=5.

| Target | tier | exec pass@1 | monty pass@1 | Δ (monty−exec) |
|---|---|---|---|---|
| **local-gemma** (4B) | hard | **1.0** (25/25) | **1.0** (25/25) | **0.0** |
| **bigpickle** (frontier) | hard | **1.0** (25/25) | **0.84** (21/25) | **−0.16** |

**Two findings, both decision-relevant:**

1. **Gemma-4-E4B holds at pass@1 1.0 on the HARD tier too** — nesting, error-handling,
   argmax, filtering, sort, and tool-selection under 13 tools with distractors. Combined
   with addendum 1 (base 18/18), the local model is now **50/50 under exec across both
   tiers**. The "structure tax" still does not bite at this complexity. Green-light
   strengthened.

2. **Monty's alpha dialect imposes a real tax — but it lands on the FRONTIER model, not
   Gemma.** Big-pickle's `monty` score dropped to 0.84, *all* failures on `longest_file`.
   Root cause (reproduced): Monty v0.0.18 rejects two idioms the strong model prefers —
   `max(items, key=lambda x: <calls a host tool>)` →
   `NotImplementedError: max() key argument: external functions are not yet supported`, and
   `dict.get()` → `AttributeError: 'dict' object has no attribute 'get'`. **Gemma scored
   monty 1.0 because it writes plainer code** — explicit `for`/`if` accumulation loops,
   not idiomatic one-liners — which stays inside Monty's supported subset. Counterintuitive
   but mechanistically clear: **the weak model's simpler style DODGES the dialect tax that
   bites the frontier model's idiomatic style.**

**Implications for the prototype (21.2b → 21.3):**
- **Code-mode is viable on this stack even today.** Under a plain restricted `exec`, Gemma
  is 1.0 on both tiers — Monty is not required to get the round-trip benefit; it only adds
  in-process isolation.
- **If using Monty:** the dialect risk is currently *unrealized* for Gemma but **fragile**
  (style-dependent — a future sample could hit `dict.get`). Cheap mitigation: a system-prompt
  note steering toward explicit loops and away from `max(key=…)` over tool calls / `dict.get`.
  Or adopt Monty later once its subset matures (it's pre-1.0, actively adding features).
- **Honest limits:** k=5 (strong but not tight CIs); Monty=1.0 for Gemma reflects the code
  it *happened* to write — higher k and broader idiom coverage would harden the claim. The
  dialect gaps are concrete and version-specific (v0.0.18).

**Net:** the make-or-break gate is **passed at the harder tier as well**, and the one real
risk the survey raised (Monty's restricted dialect) is now **measured and bounded** — it's
a frontier-style problem that this weak model largely avoids, with a cheap prompt mitigation
if Monty is adopted.

## Empirical addendum 3 — END-TO-END A/B: the round-trip hypothesis, MEASURED (2026-06-23, item 21.3)

> Not [lit-only]. The original question — *does collapsing N tool calls into one code block
> actually net faster wall-clock / fewer decode passes at 8–12 tok/s?* — answered on the live
> local model. **Yes, by a large margin, and it also fixes a reliability failure.**

**Harness:** `scripts/codemode_ab.py` runs the SAME multi-step tasks two ways against the live
Gemma endpoint, isolating the round-trip variable:
- **Arm A — flat ReAct:** one tool call per turn via a JSON action protocol
  (`{"tool":…}` → `{"result":…}` → … → `{"final":…}`); every turn is a fresh decode over a
  *growing* context — the current pattern.
- **Arm B — code-mode:** one code block chaining all calls in the sandbox, ~1 decode pass; tool
  results never enter the model context.

Run on the **raw MLX server (:8081)** to avoid the repair-proxy's tool-call parser confounding
Arm A's text protocol. 6 round-trip-heavy tasks, k=2 (12 samples/arm).

| arm | pass@1 | mean decode passes | mean wall-clock | mean tokens | failures |
|---|---|---|---|---|---|
| **flat ReAct** | **0.333** | 10.67 | **245.1 s** | 8301 | 8/12 `no_termination` |
| **code-mode** | **1.0** | 1.0 | **40.4 s** | 715 | 0 |

**Round-trip readout:** code-mode = **−90.6% decode passes · −83.5% wall-clock · −91.4% tokens ·
+0.667 pass@1.**

**Per-task shape (the important detail):**
- ReAct **failed by non-termination on 4/6 tasks** (`sum_lines`, `big_balance`,
  `orders_gt10_count`, `sum_existing_balances`) — it churned tool calls and hit the 12-turn cap
  without emitting `final`. This is **item-16's no-tool-stop / tool-churn pathology**, reproduced.
- ReAct **succeeded on 2/6** (`count_big_files`, `longest_file`) at **8 decode passes** each — and
  **even there code-mode still won** wall-clock and tokens (`count_big_files` react 8p/88s/3998tok
  vs codemode 1p/28s/498tok).
- code-mode was **12/12 correct, always 1 decode pass** — consistent with addendum 1/2 (the model
  reliably writes the whole program in one shot).

**Why the wall-clock gap is even bigger than the pass-count gap:** flat ReAct re-prefills an
ever-growing conversation (prior calls + returned tool results — `read_file` dumps whole file
bodies back into context) on every one of ~10 turns; code-mode prefills once and the file bodies
never reach the model. The mechanism is exactly what Anthropic/Cloudflare described — here measured
on a slow local decode where it matters most.

**Honest caveats:**
1. **Proxy harness, not opencode.** The JSON action loop faithfully reproduces the
   one-call-per-decode structure with mock tools; a production 21.3 must wire Arm B as a real
   opencode tool against real files and re-measure. The *structural* result (passes/wall/tokens)
   is protocol-independent and will carry; absolute pass-rates may shift.
2. **ReAct's 0.333 is a lower bound.** The strict JSON protocol may be harder for Gemma than
   native tool-calls (+ repair proxy). BUT item-16 independently found the local stack suffers the
   same no-tool-stop/churn with native tool-calls — so the non-termination is a real failure mode,
   not just a protocol artifact, and code-mode demonstrably sidesteps it.
3. **n is modest** (k=2 × 6 tasks) — but the effect is enormous and consistent (codemode 12/12;
   react fails 8/12), well outside noise.

**Verdict for item 21:** the chain is now complete and locally validated — the model **can** write
the code (21.2a/b) **and** doing so is **dramatically faster AND more reliable** than the flat loop
(21.3). Code-mode is no longer a speculative lever; it both cuts wall-clock ~5× and removes the
dominant degenerate-loop failure on this stack. The remaining work is engineering: wire it into real
opencode (Arm B as a tool over the real toolset), keep the restricted `exec` engine (Monty optional),
and re-confirm on the full harness against item-16's E0 instrumentation.

## Empirical addendum 4 — PRODUCTIONISED in real opencode (2026-06-24, item 21.4a)

> Not [lit-only]. The 21.3 win was a proxy harness with mock tools; this wires code-mode
> into **real opencode** with **real filesystem tools** and confirms the model invokes it
> natively end-to-end on the live stack.

**Shipped:**
- `scripts/codemode_exec.py` — production executor. Binds the SAME validated sandbox
  (`codegen_probe.run_exec`/`run_monty`) to REAL host-tools (`read_file`, `read_lines`,
  `list_files`, `glob`, `grep`; `bash`/`write_file` opt-in). Paths can't escape the project
  root; returns a JSON envelope (`result` + host-call log). Selftest passes.
- `.opencode/tools/codemode.ts` — opencode custom tool. Takes a `code` arg, shells to the
  executor via `Bun.spawn` (Blob stdin → JSON out), surfaces sandbox errors back to the model
  for repair. The model writes Python (which 21.2 proved Gemma does at pass@1 1.0), executed
  out-of-process — cleanly bridging opencode's TS world to the Python the model emits.

**Verified at four levels:** executor selftest (temp tree, grep, path-escape refused, bash
gating) → real-repo run (glob + 8 reads = 9 host-ops in one pass) → Bun↔Python wiring →
`opencode serve` registration (18 tools, `codemode` loaded, no errors) → **live capstone:**
`opencode run` on local Gemma through the full agent loop + repair proxy invoked `codemode`
**natively in ONE tool call**:
```python
file_list = glob('scripts/*.py')
total_lines = 0
for file_path in file_list:
    total_lines += len(read_lines(file_path, 1, None))
result = total_lines
```
→ the round-trip collapse works in production: one native tool call did a glob + 8 reads.

**Real bug the capstone caught (and fixed):** the model called `read_lines(p, 1, None)`
expecting **1-indexed** line numbers, but the host-tool used a **0-indexed slice** — silently
dropping the first line of each file (off-by-8). Fixed `read_lines` to **1-indexed inclusive**
(matching line-number intuition and the custom `read` tool's `N | line` gutter); the model's
exact code now returns the correct count (== `wc -l`). **Lesson:** host-tool API ergonomics
matter as much as the sandbox — weak models map natural-language intent onto whatever the
signature implies, so signatures must match intuition. This is a class of footgun a real run
surfaces that the mock harness could not.

**Remaining (21.4b):** the controlled A/B (21.3) showed ~5× wall-clock + reliability wins with
mock tools; the production A/B — baseline opencode vs codemode-enabled opencode on item-16's E0
instrumentation + item-17 tiers (real files, real failure-mode scoring, real tool latency) — is
the final measurement. Open question is purely whether the win survives real tool latency and
real-file complexity.

## Empirical addendum 5 — PRODUCTION A/B on real opencode: the win is real but TASK-DEPENDENT, not 5× (2026-06-24, item 21.4b)

> Not [lit-only]. This is the honest production measurement — and it **tempers addendum 3**.
> The 21.3 proxy harness banned bash; real opencode does not, which changes the result.

**Harness:** `scripts/codemode_prod_ab.py` — same multi-file tasks run two ways on the frozen
`harness_micro_fixtures` repo through REAL opencode (`opencode run --format json` + repair
proxy), reusing `harness_micro`'s config/episode/transcript machinery. Baseline = cloned global
config (read/grep/glob/list/**bash**/edit). codemode arm = same + the `codemode` tool installed +
a one-line rule nudging its use. k=1 (directional).

| task | baseline | codemode | note |
|---|---|---|---|
| count_lines | 1 call (`bash wc`), 94s ✓ | 1 call, 72s ✓ | baseline **self-batched via shell** → codemode only ~24% faster |
| def_count | **4 calls (`grep`×4), 342s** ✓ | **2 calls, 150s** ✓ | baseline round-trip-heavy → codemode **−50% calls, −56% wall** |
| find_clamp | timeout, 0 calls ✗ | timeout, 0 calls ✗ | **both degenerate** (item-16 no-tool-stop) — code-mode no cure |

**The decisive correction:** real opencode gives the model **`bash`**, which is itself a "code
mode" — on `count_lines` the model ran one `wc -l` and batched the whole task in a single call,
so codemode's edge shrank to ~24% (vs the ~5× of 21.3). **21.3's dramatic number was inflated by
a bash-less baseline** (the mock harness only exposed read/grep). When the real baseline batches
via shell, codemode is a modest win; the round-trip problem was already partly solved.

**But codemode still wins when the model does NOT self-batch:** on `def_count` the baseline chose
`grep` four times (one per file — round-trip-heavy), and codemode cut that to 2 calls and ~half
the wall-clock. Whether the model spontaneously reaches for bash vs per-file tool calls is
**inconsistent**, and codemode reliably collapses the cases where it doesn't.

**And codemode does NOT fix the core failure:** `find_clamp` timed out on BOTH arms at 600s with
**zero tool calls** — the model churned without emitting a parseable action (item-16's degenerate
generation). Code-mode helps only when the model is *productively* making tool calls; it is **not a
substitute for fixing item-16's degenerate-loop**, which gates this whole tree.

**Honest verdict for item 21:** code-mode is a **real but task-dependent** production lever, not the
blanket 5× the proxy suggested:
- **Worth enabling** for round-trip-heavy multi-file gather/scan/aggregate that the model would
  otherwise do as N separate read/grep calls (−50% calls, −20–56% wall-clock observed).
- **Marginal** when the model already self-batches via `bash` (~24%) — and on this stack bash is a
  competing, simpler "code mode" the model often picks unprompted.
- **No help** on the degenerate-loop failure (item-16), which dominates the hardest tasks.
- **Caveats:** k=1 (directional only — `find_clamp`'s double-timeout needs more samples to confirm
  it isn't task-wording noise); 3 small tasks; correctness graded by transcript substring. Raise k
  and broaden tasks (incl. structured/multi-step logic awkward in shell, where codemode should
  separate from bash more cleanly) before a final adopt/reject.

**Recommendation:** keep `codemode` available (it never lost and clearly won the non-self-batched
case), but **do not expect it to move the headline pass-rate until item-16's degenerate-loop is
fixed** — sequence it after item 16, consistent with the repo's gating. The 21.3 ~5× should be
cited as a *bash-less* upper bound, not the production expectation.

## Sources

Primary: Anthropic `engineering/code-execution-with-mcp`; Cloudflare `blog/code-mode`,
`code-mode-mcp`, `python-workers`; `github.com/pydantic/monty`,
`pydantic.dev/articles/pydantic-monty`; HF `blog/structured-codeagent`;
`github.com/xingyaoww/code-act`; arxiv 2507.03160v4, 2510.03847, 2512.15943v1, 2510.13859.
Secondary/blog: `simonwillison.net/2026/Feb/6/pydantic-monty/`,
`workos.com/blog/cloudflare-code-mode-cuts-token-usage-by-81`, `blaxel.ai`, `blinkops.com`.
