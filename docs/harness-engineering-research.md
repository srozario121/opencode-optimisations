# Harness-engineering lever survey — research findings (TODO item 11)

Research pass for **TODO item 11** ("Harness engineering for opencode + local
Gemma 4"). A sequel to items 8–10. Those items **fixed** the stack — the model
(Gemma 4 E4B QAT) and the serving engine (mlx-lm 0.31.3 via Apple MLX) — and
exhausted the **serving-engine** lever (item 9 tuned MLX; item 10 compared whole
engines; nothing beat the baseline). This item turns to the one lever left: the
**harness** — everything on the **opencode side** that we can change *without*
swapping the model or the engine.

The question it answers: *which opencode-side levers are most likely to raise the
local coding loop's task pass-rate, ranked so the experiment can test only the
top 3–4 single levers one at a time.* The downstream experiment (a tiny curated
SWE-bench-Lite subset scored pass/fail on a 16 GB M1) tests one lever per run, so
the **top of this ranking is the entire deliverable** — a wrong #1 wastes a
~6–12 h benchmark slot.

**Carry-through constraints (from items 8–10, non-negotiable).** Fully local /
offline at serve time (`HF_HUB_OFFLINE=1`, no model-hub egress); **16 GB M1**
(Apple Silicon, Metal) with the established envelope — **~8–12 tok/s decode,
~160 tok/s prefill, a ~40–50K-token Metal-OOM ceiling** (see the *Throughput*
section of `docs/opencode-local.md`); single-user interactive opencode workload
against the OpenAI-compatible `127.0.0.1` MLX endpoint; **tool/function-calls
must stay reliable** (the repair proxy `scripts/mlx_repair_proxy.py` exists
because Gemma 4 tool-calling is fragile on mlx-lm — #1096/#1125). **Model +
serving engine are FIXED**: no model swap, no engine swap, no KV-quant / batching
/ speculative decoding — all are items 9/10's domain, out of scope here.

This is the harness analogue of `docs/local-inference-engines-research.md` (the
item-10 engine survey) and `docs/opencode-config.md` (the already-verified map of
opencode config knobs). It **builds on** both rather than repeating them.

> **Time-sensitivity caveat (dominant).** opencode moves near-daily; everything
> here is verified against **opencode 1.17.7** (the repo's pin) as of June 2026.
> The GitHub org renamed `sst/opencode` → `anomalyco/opencode` (same project;
> `sst/opencode` redirects). Re-verify config keys on upgrade. External
> harness-research evidence is a 2024–2026 snapshot; most numbers come from
> frontier-model studies (SWE-agent on GPT-4), so the *direction* transfers to a
> small local model but the *magnitude* will differ — generally **larger**, since
> weaker models are more harness-sensitive (see §The small-model thesis).

---

## The 7 lever categories

The "harness" = the opencode↔model surface only. Item 11 maps and ranks tunable
levers across exactly these seven categories:

1. **System / agent prompt** — content, length, terseness, tool-use instructions.
2. **Tool definitions & their descriptions** — wording, schema, examples.
3. **Which tools are exposed** — minimal vs full toolset; disabling rarely-used tools.
4. **Context-window / compaction / prune policy** — `compaction.*`, provider `limit.*`.
5. **Tool-result truncation limits** — the repo's custom `read`/`grep` reduction (`RTK_READ_LEVEL`, grep caps).
6. **Retry / repair behaviour** — the repair-proxy path.
7. **Temperature / top-p / top-k sampling** — set opencode-side per request (item-8 defaults are baseline).

---

## Per-lever matrix

Legend: evidence **strong** = primary paper / official table / verified opencode
source · **medium** = preprint / well-documented blog study / verified-but-indirect ·
**weak** = practitioner-blog assertion / vendor figure. "Cost to try" = effort to
change + measure one lever run on this stack.

| # | Lever | Category | What opencode exposes | Expected effect (small local model) | Evidence | Cost to try | Rank |
|---|---|---|---|---|---|---|---|
| L1 | **Minimal, well-shaped toolset** (deny rarely-used tools per agent: keep read/grep/edit/bash/list; drop task/webfetch/websearch/lsp/skill/doom_loop) | 3 | **Strong** — `permission` map `allow`/`ask`/`deny`, global `*` + per-tool, per-agent override (legacy boolean `tools` still works) | **Large.** Fewer tools = smaller decision surface + fewer schemas in context. A *bad/extra* tool is worse than none (SWE-agent: iterative-search 12.0% < no-search 15.7%). Weak models gain most. Also frees context tokens on a tiny window. | **Strong** — SWE-agent Table 3; mini-swe-agent; Harness-Bench weak-model variance | **Low** — one `permission`/`agent` config block; no code | **1** |
| L2 | **Lower temperature** (0.0–0.2 vs item-8 default) | 7 | **Strong** — per-agent `temperature` + `top_p` documented keys, forwarded to the openai-compatible provider | **Medium-high & cheap.** Greedy/near-greedy is the function-calling standard (BFCL temp=0.001) for **tool-call validity** + reproducibility — directly de-risks the fragile Gemma-4 tool path. Also makes scoring deterministic. | **Strong** — BFCL methodology; local-LLM tool-call evals (temp=0); SWE-agent uses greedy | **Very low** — one config key | **2** |
| L3 | **Custom/terser system+agent prompt** (per-agent `prompt`: terse, explicit tool-use protocol, "read before edit", small-scope edits) | 1 | **Strong** — per-agent `prompt` file **replaces** the base system prompt entirely; `instructions` + AGENTS.md layer on top | **Medium.** Prompt scaffolding matters *less* than tool design on a fixed model (SWE-agent demo removal only −1.7pp) but small models are prompt-robustness-sensitive, and the opencode default prompt is long/frontier-tuned — a tight model-appropriate prompt can recover tokens + focus behaviour. Risk: a full override can *drop* useful default tool guidance → tool-call regressions. | **Medium** — SWE-agent demo ablation (small); system-prompt-robustness preprint; prompt-replacement semantics verified | **Low-med** — write a prompt file; risk of tool-call regression needs the round-trip check | **3** |
| L4 | **Stale-tool-output pruning** (`compaction.prune: true`, tune `compaction.reserved`) | 4 | **Strong** — `compaction` object: `auto` (default true), `prune` (default **false**), `reserved`; provider `limit.context/output` | **Medium.** Collapsing/pruning stale observations directly raised SWE-agent success (+3.0pp last-5-obs vs full history); "context rot" degrades long runs. Most valuable precisely because the window is tiny + OOM-bounded. But the curated subset is **pre-screened <30K context** to dodge OOM, so episodes may rarely fill the window → effect could be muted on *this* benchmark. | **Strong (general) / medium (this subset)** — SWE-agent Table 3; context-pruning preprints; Anthropic context-engineering | **Low** — one config flag | **4** |
| L5 | **Tighter tool-result truncation** (`RTK_READ_LEVEL=aggressive`, lower `RTK_GREP_MAX_*`) | 5 | **Repo-only** — opencode has **no native** truncation knob (#25337/#2375); the repo's custom `.opencode/tools/read.ts`/`grep.ts` already do reduction (`RTK_READ_LEVEL`, `RTK_GREP_MAX_COLUMNS/RESULTS`) | **Low-medium, double-edged.** Window-sizing has a sweet spot — *too little* context also hurts (SWE-agent 30-line 14.3% < 100-line 18.0%, but full-file 12.7% < 100-line too). The baseline already runs `minimal`; going `aggressive` risks dropping the line the model needs. More likely a token-saver than a pass-rate lever on a pre-screened small subset. | **Medium** — SWE-agent window-size ablation (both directions hurt) | **Very low** — one env var | **5** |
| L6 | **Repair/retry tuning** (proxy retry count; keep proxy ON) | 6 | **Proxy-only** — opencode does **not** natively retry malformed tool calls (turn just stops, #1388/#15906/#29142); provider 5xx retried but not user-configurable (#26675 closed). The repo's `mlx_repair_proxy.py` is the only repair seam. | **Reliability floor, not a pass-rate lever.** The proxy already keeps tool-calls alive; it's a *guardrail* the other levers depend on, not a variable to sweep for score. Turning it off would regress reliability (the constraint forbids). Retry only helps at temp>0 (fresh sample). | **Medium** — proxy behaviour verified in opencode-local.md; opencode-native-retry-absence verified via issues | **Med** — proxy code change + careful tool-call validation | **6 (keep fixed)** |
| L7 | **top-k / top-p sweep** (beyond L2's temperature) | 7 | **Partial** — `top_p` is a documented key; **`top_k` is plugin-only** (`chat.params` hook), and an OpenAI-compatible `/v1` endpoint typically **ignores top_k** | **Low / uncertain.** Secondary to temperature for tool-call validity; the highest-value sampling lever is temperature (L2). top_k likely never reaches mlx-lm's openai endpoint. | **Weak** — directional blog guidance only; no controlled small-model deltas | **Med** — top_k needs a plugin; top_p effect small | **7 (skip)** |

---

## Ranked lever shortlist (the headline output)

Ranking criterion: **expected effect × evidence strength × low cost-to-try**,
with a hard **penalty on anything that risks tool-call reliability** (the
non-negotiable constraint) and a discount for levers whose effect is muted by the
experiment's **pre-screened <30K-context** subset.

**Test these single-lever, one at a time, vs the frozen baseline — in this order:**

1. **L1 — Minimal, well-shaped toolset.** Strongest evidence base (SWE-agent's
   tool-design ablations are the largest single swings on a fixed model;
   Harness-Bench shows weak models vary most across harnesses), trivial to apply
   (one `permission`/`agent` block, no code), and directly attacks the two things
   that hurt a tiny slow model most: a bloated decision surface and wasted context
   tokens. The "a bad tool is worse than none" result (12.0% < 15.7%) makes
   *removing* low-value tools a high-confidence net positive. **Top pick.**

2. **L2 — Lower temperature (→ ~0.0–0.2).** Almost-free (one key), strong evidence
   (greedy is the function-calling standard), and it hardens the *fragile* part of
   this exact stack — Gemma-4 tool-call validity — while also making the pass/fail
   scoring more deterministic. The clearest low-risk win.

3. **L3 — Terser, model-appropriate system/agent prompt.** Higher ceiling than its
   ablation magnitude suggests *because* the opencode default prompt is long and
   frontier-tuned, and small models are prompt-robustness-sensitive. Carries the
   only real reliability risk in the top group (a full `prompt` override can drop
   default tool guidance), so it **must** pass the tool-call round-trip check —
   hence ranked behind the two safer levers.

4. **L4 — Stale-tool-output pruning (`compaction.prune: true`).** Strong general
   evidence (+3.0pp last-5-obs in SWE-agent; context-rot literature), one-flag
   cost. Ranked 4th only because the subset is pre-screened to stay *under* the
   OOM ceiling, so episodes may not fill the window enough for pruning to bite —
   the right 4th lever to test, but with tempered expectations on *this* benchmark.

**Documented-but-untested (below the cut — do not spend a benchmark slot):**

- **L5 — Tighter tool-result truncation** (`RTK_READ_LEVEL=aggressive`). The
  baseline already runs `minimal`; going more aggressive is double-edged
  (too-little-context also hurts pass rate per SWE-agent's window-size sweet spot)
  and is more a token-saver than a score lever on a small pre-screened subset.
- **L6 — Repair/retry tuning.** **Held FIXED, not swept.** The repair proxy is the
  reliability *floor* every other lever stands on; it stays ON for all runs (the
  constraint forbids breaking tool-calls). Not a variable.
- **L7 — top-k / top-p sweep.** Secondary to L2; `top_k` is plugin-only and an
  OpenAI-compatible endpoint typically ignores it; weakest evidence. Skip.

---

## The small-model thesis (why harness work pays off here)

The premise of item 11 — that harness quality matters **more** for a small/weak
local model than for a frontier model — is well supported:

- **Harness-Bench** (arXiv:2605.27922), across 8 model backends × 106 tasks,
  found configurable harnesses spanned a **23.8pp** gap (76.2% vs 52.4%), and
  explicitly that "weaker or less robust backends show larger variance across
  harnesses … more sensitive to the surrounding execution substrate." Strong.
- **aider edit-format benchmark**: a *harder* harness format costs weaker models
  disproportionately — GPT-3.5-turbo-0301 dropped **46% → 30% (−16pp)** moving
  from the `whole` edit format to `diff`; aider's stated rule is to use the
  simpler `whole` format for weaker/local models and it tracks "percent using
  correct edit format" as a first-class metric. Strong.
- **Counterweight (kept honest):** the ALE-Claw study found model choice ≈ **3×**
  the harness effect (18.0pp model spread vs 5.3–6.0pp harness spread) — but that
  was on *frontier* models, and the authors list "does a richer harness help
  weaker models more?" as an open question they did *not* measure. So harness work
  is real but bounded: expect single-digit-to-low-double-digit pass-count moves,
  not a transformation.

---

## Per-category findings (cited)

### 1. System / agent prompt

**opencode configurability (strong).** A per-agent `prompt` key (file ref, e.g.
`"prompt": "{file:./prompts/edit.txt}"`, or markdown frontmatter / `.opencode/agent/*.md`)
gives full control of wording/length — and importantly, an agent's own `prompt`
**replaces** the provider/default system prompt entirely (it is not appended), so
this is the lever for total prompt control. `instructions` (array of paths/globs)
and auto-discovered `AGENTS.md`/`CLAUDE.md` (incl. the repo's `~/.claude/CLAUDE.md`,
already auto-loaded — see `docs/opencode-config.md` §3) *augment* the base prompt
rather than replacing it. ([opencode agents docs], [opencode config docs])

**Expected effect (medium).** On a *fixed* model, prompt scaffolding moves the
needle less than tool design: SWE-agent's removal of the in-context demonstration
cost only **−1.7pp** (18.0 → 16.3 on SWE-bench Lite), smaller than any tool-design
ablation. But two facts raise the ceiling for *this* setup: (a) small models are
sensitive to system-prompt robustness (arXiv:2502.12197), and (b) opencode's
default base prompt is long and frontier-model-tuned, so a tight, model-appropriate
prompt both focuses behaviour and reclaims scarce context tokens. **Risk:** because
a custom `prompt` *replaces* the default, a naïve override can drop opencode's
built-in tool-use guidance and *regress* tool-calls — so this lever must pass the
tool-call round-trip check.

### 2. Tool definitions & their descriptions

**opencode configurability (strong, but scoped).** Custom tools at
`.opencode/tools/*.ts` let the author set **both** the free-text `description` and
the Zod-style `args` schema (each arg `.describe()`-able); the filename becomes the
tool name and shadows a built-in of the same name. ([opencode custom-tools docs];
the repo already exercises this — `.opencode/tools/read.ts`/`grep.ts`, see
`.opencode/README.md`.) **Caveat:** this only controls *custom* tools — you
**cannot** rewrite the description text of the built-in `read`/`grep`/`bash`/`edit`
via config; for those, the only config-level control is enable/disable/permission
(category 3).

**Expected effect (medium).** Tool-description/schema quality is a real lever —
"Learning to Rewrite Tool Descriptions" (arXiv:2602.20426) and schema-first tool-API
studies (arXiv:2603.13404) report selection/execution gains — and the SWE-agent ACI
result that *interface shape* swings pass rate 1.7–7.7pp is the strongest evidence
that how a tool is presented matters. **Why this isn't a top-ranked single lever
here:** the highest-value built-in tool descriptions aren't config-editable, and the
repo's custom `read`/`grep` already have hand-tuned descriptions. Rewriting one
description is a fiddly, hard-to-attribute change vs. the cleaner L1 (drop tools
wholesale). Folded into L1's "well-shaped toolset" rather than ranked on its own.

### 3. Which tools are exposed

**opencode configurability (strong).** A `permission` map (`allow`/`ask`/`deny`,
global `*` plus per-tool keys: `read, edit, glob, grep, bash, task, skill, lsp,
question, webfetch, websearch, external_directory, doom_loop`), per-agent overrides
(agent rules win), and the deprecated-but-honored boolean `tools` map. A minimal
toolset is one config block: `"permission": { "*": "deny", "read": "allow",
"grep": "allow", "edit": "allow", "bash": "allow", "list": "allow" }`.
([opencode permissions docs])

**Expected effect (strong → this is L1, rank 1).** The single best-evidenced
harness lever for weak models. SWE-agent Table 3: a *poorly-shaped extra* tool is
worse than none (iterative-search **12.0%** < no-search **15.7%**), and the whole
ACI thesis is that a tight LM-friendly tool surface beats a sprawling one
(full ACI 18.0% vs 3.8% RAG baseline). mini-swe-agent shows a *single bash tool*
suffices for strong models; for a weak model the win comes from removing
low-value, schema-heavy, rarely-used tools (`task`, `webfetch`, `websearch`, `lsp`,
`skill`, `doom_loop`) that bloat the decision surface and eat the tiny context.
Harness-Bench confirms weaker backends vary most across exactly this kind of
substrate change. Trivial cost, high confidence → **rank 1.**

### 4. Context-window / compaction / prune

**opencode configurability (strong).** The `compaction` object: `auto` (default
`true` — compact when full), `prune` (default **false** — drop OLD tool outputs),
`reserved` (buffer so compaction itself doesn't overflow); plus provider/model
`limit.context` / `limit.output` (which a custom openai-compatible provider must
declare — see `docs/opencode-config.md` §1/§2). The compaction *threshold* itself
isn't separately exposed (open request #8140). ([opencode config docs])

**Expected effect (medium; strong in general, muted on this subset).** Pruning
stale observations directly helped SWE-agent (**last-5-obs 18.0% vs full-history
15.0%, +3.0pp**); context-pruning preprints (arXiv:2606.10209, arXiv:2605.30785)
report pruned configs beating full-context on long-horizon tool tasks; "context
rot" (a clean prompt scoring 98.1 dropping to 64.1 spread across a long run)
motivates shedding history. On a tiny slow window this is high-value in
principle — **but** item 11's curated subset is pre-screened to stay **<30K
context** (to dodge the 40–50K OOM cliff), so episodes may not fill the window
enough for `prune` to bite. Good 4th lever, tempered expectations. **L4, rank 4.**

### 5. Tool-result truncation limits

**opencode configurability (repo-only).** opencode has **no native** knob to shrink
tool output — built-in `read` (~2000 lines, ~2000-char/line) and `grep`/`glob`
limits are hardcoded, with open requests to make them configurable (#25337, #2375).
The repo fills this gap with custom `.opencode/tools/read.ts`/`grep.ts` that reduce
output via rtk/ripgrep, tunable by `RTK_READ_LEVEL` (`none`/`minimal`/`aggressive`,
default `minimal`) and `RTK_GREP_MAX_COLUMNS`/`RTK_GREP_MAX_RESULTS` (see
`.opencode/README.md`). `compaction.prune` (category 4) drops *old* outputs from
history but doesn't shrink a *fresh* result — only the custom tools do.

**Expected effect (low-medium, double-edged).** Window-sizing has a *sweet spot*:
SWE-agent's file-viewer ablation shows **both** too-little (30-line **14.3%**) and
too-much (full-file **12.7%**) underperform the 100-line setting (**18.0%**). The
baseline already runs `minimal`; pushing to `aggressive` risks truncating the exact
line the model needs (and `aggressive` can empty some files — see
`.opencode/README.md`). More a token-saver than a pass-rate lever on a small
pre-screened subset. **L5 — documented-but-untested.**

### 6. Retry / repair behaviour

**opencode configurability (proxy-only; native repair absent — verified).**
opencode does **not** natively retry malformed/failed tool calls — the turn simply
stops (open requests #1388 "Auto Tool Failure Retry", #15906, #29142); provider
5xx/network errors *are* retried with backoff but with **no** user-configurable
max (#26675 *closed as not-planned*). The repo's `scripts/mlx_repair_proxy.py` is
therefore the only repair seam: it re-parses Gemma's `<|tool_call>…<tool_call|>`
markers into structured `tool_calls`, catches the #1125 `ValueError` 500, retries
(only helps at temp>0), and degrades to a graceful empty turn so the session
survives (see `docs/opencode-local.md` §Tool-call repair proxy).

**Expected effect (reliability floor, not a score lever).** This is the
*guardrail* the other levers depend on, not a variable to sweep for pass rate.
Keep the proxy **ON** for every run; turning it off would regress tool-call
reliability, which the constraints forbid. (Note the temperature interaction: the
proxy's retry only helps when temp>0 — a tension with L2's push toward greedy; at
temp≈0 the proxy's value is its parse-repair, not its resample.) **L6 — held
fixed, not swept.**

### 7. Temperature / top-p / top-k sampling

**opencode configurability (strong for temp/top_p; partial for top_k).** Per-agent
`temperature` and `top_p` are documented keys, forwarded to the openai-compatible
provider. `top_k` is **not** a documented config key — it's reachable only via the
`chat.params` plugin hook, and an OpenAI-compatible `/v1` endpoint typically
**ignores** `top_k` anyway. ([opencode config docs], [opencode providers docs]).
The repo's own template already recommends low temperature (0.1–0.3) for the local
agent (`docs/opencode-local.md` §least-privilege agent).

**Expected effect (strong for temperature → L2, rank 2; weak for top_k → L7,
skip).** Greedy/near-greedy decoding is the **standard** for function-calling
evaluation — BFCL fixes temperature at **0.001** for reproducibility and uses
AST-validity checking; local-LLM tool-call evals fix temp=0 to isolate
schema-validity. Lower temperature is the cheapest, best-evidenced way to harden
the *fragile* Gemma-4 tool-call path on this exact stack, and it makes the pass/fail
scoring deterministic. (One nuance: greedy occasionally emits an invalid call that
a higher-temp resample recovers — so the relationship isn't strictly monotonic at
the extreme — but in aggregate lower temp improves validity.) `top_p`/`top_k`
sweeps are secondary and weakly evidenced. **L2 (temperature) rank 2; L7 (top_k/
top_p) documented-but-untested.**

---

## Notes on levers that are N-A / documented-but-untested

- **Built-in tool-description editing** — N-A by config: only *custom* tool
  descriptions are author-controlled; built-in `read`/`grep`/`bash`/`edit`
  descriptions can't be rewritten via opencode config (folded into L1).
- **Native tool-call retry/repair** — N-A: opencode has none (#1388/#15906); the
  repair proxy substitutes and stays fixed (L6).
- **Auto-compaction threshold** — N-A: not separately exposed (#8140); only
  `auto`/`prune`/`reserved` are tunable.
- **`top_k`** — effectively N-A: plugin-only and ignored by the OpenAI-compatible
  endpoint (L7, skip).
- **L5 (aggressive truncation), L7 (top_p/top_k)** — documented-but-untested:
  below the single-lever cut; lower expected effect and/or double-edged on a
  pre-screened small subset.

---

## Sources

### opencode (config configurability — verified against 1.17.7)
- [opencode config docs](https://opencode.ai/docs/config/) — `compaction.*`, `temperature`, `top_p`, `limit`
- [opencode agents docs](https://opencode.ai/docs/agents/) — per-agent `prompt`, `instructions`
- [opencode permissions docs](https://opencode.ai/docs/permissions/) — `permission` allow/ask/deny, per-agent overrides, deprecated `tools` map
- [opencode custom-tools docs](https://opencode.ai/docs/custom-tools/) — author-controlled `description` + Zod `args`; filename-shadowing
- [opencode providers docs](https://opencode.ai/docs/providers/) — openai-compatible provider, `limit`, options passthrough
- opencode issues (native gaps): [#1388 auto tool-failure retry](https://github.com/anomalyco/opencode/issues/1388), [#15906 retry invalid tool-call](https://github.com/anomalyco/opencode/issues/15906), [#29142 invalid-schema tool args](https://github.com/anomalyco/opencode/issues/29142), [#26675 configurable retry — closed/not-planned](https://github.com/anomalyco/opencode/issues/26675), [#25337 read MAX_LINE_LENGTH hardcoded](https://github.com/anomalyco/opencode/issues/25337), [#2375 glob/grep max-lines](https://github.com/anomalyco/opencode/issues/2375), [#8140 configurable compaction threshold](https://github.com/anomalyco/opencode/issues/8140), [#13770 truncation problems](https://github.com/anomalyco/opencode/issues/13770)
- Repo-verified surface: `docs/opencode-config.md`, `.opencode/README.md`, `docs/opencode-local.md` (this repo, prior already-verified passes)

### Harness-engineering prior work
- [SWE-agent / ACI paper (arXiv:2405.15793)](https://arxiv.org/abs/2405.15793) and [NeurIPS 2024 camera-ready PDF](https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf) — Table 3 ablations (edit linting +3.0pp, summarized vs iterative search, window-size sweet spot, last-5-obs context mgmt +3.0pp, demo removal −1.7pp)
- [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent) — bash-only scaffold, >74% SWE-bench Verified with a frontier model
- [Harness-Bench (arXiv:2605.27922)](https://arxiv.org/html/2605.27922v1) — 23.8pp cross-harness gap; weaker backends show larger variance (the central small-model evidence)
- [aider edit-format benchmarks](https://aider.chat/docs/benchmarks.html) and [leaderboards](https://aider.chat/docs/leaderboards/) — GPT-3.5 46%→30% (whole vs diff); edit-format-correctness as a first-class metric; `whole` recommended for weak/local models
- ["Does the Harness Matter?" — ALE-Claw study](https://agents-last-exam.org/blogs/harness-matters) — model ≈ 3× harness effect (frontier models); minimal harness slightly better + far cheaper; "does richer harness help weaker models" left open
- [Berkeley Function Calling Leaderboard CHANGELOG (temp=0.001)](https://github.com/ShishirPatil/gorilla/blob/main/berkeley-function-call-leaderboard/CHANGELOG.md) and [BFCL leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) — greedy standard for tool-call validity
- [Local-LLM tool-calling eval (temp=0)](https://www.jdhodges.com/blog/local-llms-on-tool-calling-2026-pt1-local-lm/) — schema-aware accuracy, small-model finding (blog; small N)
- [Learning to Rewrite Tool Descriptions (arXiv:2602.20426)](https://arxiv.org/html/2602.20426) and [Schema-First Tool APIs (arXiv:2603.13404)](https://arxiv.org/html/2603.13404v1) — tool-description/schema quality as a lever (preprints)
- [System Prompt Robustness (arXiv:2502.12197)](https://arxiv.org/pdf/2502.12197) — small-model prompt sensitivity
- [Less Context, Better Agents (arXiv:2606.10209)](https://arxiv.org/html/2606.10209) and [Agent-Compatible Context Management (arXiv:2605.30785)](https://arxiv.org/html/2605.30785) — pruning beats full-context on long-horizon tasks (preprints)
- [Anthropic — Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) and [tool-use automatic context compaction cookbook](https://platform.claude.com/cookbook/tool-use-automatic-context-compaction) — compaction/context-rot (official docs/guidance)

> **Confidence summary.** L1/L2 rankings rest on **strong** primary evidence
> (SWE-agent Table 3, Harness-Bench, BFCL) + **verified** opencode configurability.
> L3/L4 rest on strong-general / medium-for-this-subset evidence. Could **not**
> independently verify exact deltas for the tool-description and context-pruning
> preprints (abstract-level only — medium), nor the small-N local-LLM blog eval
> (weak). The "model ≈ 3× harness" counterweight is from a single well-documented
> blog study on frontier models. Re-verify all opencode keys on upgrade past
> 1.17.7.
