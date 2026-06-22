# Tiny `small_model` / session-title model — research findings (TODO item 12, task A)

Research pass for **TODO item 12, task A** ("Cheapen or eliminate session-title
generation"). Investigates which sub-1B (or otherwise tiny) local LLM is the best
fit for opencode's `small_model` / session-title slot on the item-8 stack:
opencode + Gemma 4 E4B QAT via MLX (`mlx_lm.server`) on a 16 GB M1, fully local /
offline (`HF_HUB_OFFLINE=1`), co-residing with the main coding model
(`mlx-community/gemma-4-E4B-it-qat-4bit`, ~6.3 GB resident). The title call only
needs to emit a short, usable session title from a short conversation prefix — it
does **not** need reliable tool/function-calling.

Produced by the `deep-research` harness (2026-06-20): 5 search angles, 21
sources, 88 extracted claims, 25 put through 3-vote adversarial verification.

> **Verification caveat.** The verification stage hit an upstream **session
> limit** partway through and the synthesis stage never ran. Of the 25 verified
> claims, **10 confirmed** stand (nine 3-0, one 2-1). The other 15 are listed by
> the harness as "refuted", but **13 of them were `0-0 (3 abstain)`** — the
> voters were cut off by the session limit and never actually judged them. Those
> are therefore **UNVERIFIED, not false**, and several (exact repo SHAs, exact
> weight-file sizes for the `mlx-community` 270M-it repos) are directly relevant.
> They are kept below, clearly marked. Only **2 claims were genuinely refuted**
> (a non-vote). Re-run the research to verify the abstained claims before relying
> on a specific repo SHA other than the one confirmed in §(a).

---

## TL;DR — the recommendation

**Pick (if a tiny model is served): Gemma 3 270M instruct, 4-bit MLX.** It is a
genuine 270M-parameter instruct model (170M embedding + 100M transformer, 256k
vocab), Apple-Silicon-native via MLX, with a 4-bit on-disk footprint of only
**~150–245 MB** — ~25–40× smaller than E4B. It co-resides with E4B trivially
(0.25 GB vs E4B's 6.3 GB, both well under 16 GB) and Google ships an
instruction-tuned checkpoint that "follows general instructions right out of the
box", which clears the only capability gate this slot needs (produce a short
title). Concrete pinnable repo with a **confirmed revision SHA** in §(a).

**But the recommended *default* is the terminal alternative: disable auto-title
generation (candidate path 1) first, and only serve the 270M model if the
auto-title is judged worth the cost.** Reasoning (full argument in §(d)): on this
stack **prefill — not decode — is the wall**, and session start is precisely when
the first real turn is cold-prefilling 9k+ tokens at ~0.4 tok/s. *Any* second
model, even a 270M one, contends for the **single Metal GPU** during that exact
window. Disabling title-gen removes that contention for **zero** memory and zero
new moving parts; serving a tiny model shrinks the stall (vs the ~157s caused by
the *full E4B* doing titles today) but does not eliminate GPU contention and adds
a second `mlx_lm.server` process to supervise. This research pins the candidate;
**the measurement in candidate paths 1 vs 2 decides** — both "disable" and "serve
270M" are valid terminal outcomes per the item's adopt-if-better rule.

---

## (a) The model pick — concrete, pinnable, offline

The single best candidate for the slot is a **Gemma 3 270M *instruct*, 4-bit MLX**
quantization. Two production-ready repos exist; both download once and then serve
under `HF_HUB_OFFLINE=1`, pulled via the existing `scripts/mlx.sh` curl-resume +
sha256(lfs-oid) path pinned to a revision.

**Primary pick (confirmed SHA) — `lmstudio-community/gemma-3-270m-it-MLX-4bit`:**

- Confirmed commit SHA **`99d11aebe39a4437f97e21a5216bd1eb3b8f7607`**
  (created / last-modified 2025-08-14) — *verified 3-0*, so it is pinnable today
  without a further lookup.
- 4-bit MLX quantization of `gemma-3-270m-it`, single-file `model.safetensors`,
  **245 MB** on disk — *verified 3-0*.
- Instruct (`-it`) variant, Apple-Silicon-optimized MLX format.
  — <https://huggingface.co/lmstudio-community/gemma-3-270m-it-MLX-4bit>

**QAT-consistent alternative (SHA to confirm at pull time) —
`mlx-community/gemma-3-270m-it-qat-4bit`:** the `mlx-community` org publishes both
`gemma-3-270m-it-4bit` and a **QAT** 4-bit variant `gemma-3-270m-it-qat-4bit`
(*verified 3-0* that both repos exist). The QAT variant is the more natural match
to the main model (E4B is also QAT 4-bit), but its exact revision SHA and weight
size were **not verified** (the voters abstained at the session limit — see
caveat). One *unverified* claim puts `mlx-community/gemma-3-270m-it-4bit` at SHA
`ff1143e3a10547c9f2129e94ca37059b096b23f4` (last-modified 2025-08-14) with a
**~151 MB** `model.safetensors` (150,939,130 bytes). `scripts/mlx.sh cmd_pull`
already resolves the file list + lfs-oids from the HF API at the pinned revision,
so the exact SHA for whichever `mlx-community` repo is chosen can be confirmed at
pull time — but until then, **prefer the confirmed-SHA `lmstudio-community` repo
above** for a no-surprises pin.
  — <https://huggingface.co/collections/mlx-community/gemma-3-270m>

**Do NOT use `mlx-community/gemma-3-270m-4bit`** — *verified 3-0* that it is
converted from the **base** `google/gemma-3-270m`, not the instruct (`-it`)
variant. The slot needs instruction-following, so the base model is the wrong
artifact even though the name looks close.

**Larger fallback — `mlx-community/gemma-3-1b-it-qat-4bit`:** a 4-bit QAT MLX
conversion of `google/gemma-3-1b-it-qat-q4_0`, **733 MB** on disk (*both verified
3-0*). More capable but ~3× the footprint and ~3.7× the parameters of the 270M
pick — unnecessary for title generation and more GPU contention. Use only if the
270M instruct model produces unusably poor titles in the path-2 measurement.
  — <https://huggingface.co/mlx-community/gemma-3-1b-it-qat-4bit>

## (b) Memory-budget check

> **Empirical update (2026-06-21, item 12 implementation).** Measured on the
> actual stack: the served QAT 270M pick's second `mlx_lm.server` worker is
> **~684 MB RSS** — more than the ~0.25 GB *weight-only* estimate below, because
> it is a whole second Python+mlx runtime, not just weights (274 MB on disk).
> Still a wide margin co-resident with E4B (~6.3 GB) under 16 GB, nowhere near the
> OOM cliff. Capability gate passed (a usable title from a short prompt). Details
> in the **Latency optimizations** section of `docs/opencode-local.md`.


The 16 GB unified-memory budget already holds **E4B (~6.3 GB resident) + the
mlx-lm runtime**, and item-9 found a **~40–50K-token Metal-OOM cliff** on this
machine driven by KV-cache growth during long agentic sessions. A second resident
model competes for the *same* unified memory **and** the single Metal GPU.

- **Static weight footprint of the pick is negligible:** the 270M 4-bit weights
  are **~150–245 MB** (vs E4B's 6.3 GB). Even with a second `mlx_lm.server`
  Python runtime (a few hundred MB of process overhead) and the title call's tiny
  KV cache (the title prompt is a short conversation prefix, well under 2K
  tokens), the second model adds well under **~1 GB** resident. E4B 6.3 GB + 270M
  ~0.25 GB + two runtimes leaves comfortable headroom under 16 GB.
- **The real risk is not the tiny model's weights — it is the main loop's KV
  cliff.** The 270M model does not move the ~40–50K-token OOM ceiling on the E4B
  side; that ceiling is set by E4B's own KV growth (item-9 finding). The tiny
  model's own KV footprint for a short title prompt is trivial. So co-residence
  is safe **as long as the title model is the 270M (or at most 1B) class and its
  context stays short** — which it is by construction (titles are generated from a
  short prefix, not the full agentic transcript).
- **Caveat:** `mlx_lm.server`'s lack of a hard KV-cache bound was raised as a
  candidate cause of the cliff but that specific claim was **unverified**
  (abstained at the session limit). The budget argument above does not depend on
  it — it depends only on the verified weight sizes.

**Conclusion:** the 270M 4-bit pick co-resides with E4B under 16 GB with wide
margin and does not push the agentic loop toward the OOM cliff. The memory budget
is **not** the deciding constraint — GPU contention during session start is (§d).

## (c) Minimal capability gate — can it title?

The slot's bar is low by design: emit a short, usable session title from a short
conversation prefix. **Full tool-call reliability is explicitly NOT required** for
this slot (unlike the main model, which needs the repair proxy). Evidence the
270M instruct pick clears this gate:

- Google ships an **instruction-tuned** Gemma 3 270M checkpoint that "follows
  general instructions right out of the box" (*verified 3-0*) — alongside the
  pretrained checkpoint. Short summarization / "give this a title" is squarely in
  scope for an instruct model.
- It is a real 270M-parameter model (170M embedding + 100M transformer, 256k
  vocab) — *verified 3-0* — so it is small enough to be fast on the title call yet
  is a full instruct model, not a toy.
- **Official QAT INT4 checkpoints exist with "minimal performance degradation"**
  (*verified 2-1*), so the 4-bit quantization does not meaningfully erode the
  instruction-following needed here.
- **Gate is pass/fail, checked manually in path 2:** the path-2 measurement should
  spot-check that titles are coherent and on-topic. Gemma 3 270M is widely used as
  a fine-tune base and for narrow summarization tasks; out-of-the-box title
  quality for an *un-fine-tuned* 270M is the main open risk. If titles are
  unusable, fall back to the 1B-it pick (§a) or to disabling title-gen (§d).

## (d) Terminal alternative — disable auto-title generation (recommended default)

opencode's **`small_model` config key configures a separate model for lightweight
tasks like title generation** (*verified 3-0*,
<https://opencode.ai/docs/config/>). So the slot is real and configurable. The
question is whether to fill it with a tiny model (path 2) or to **disable
auto-title generation entirely** (path 1).

**The case for disabling (path 1) as the default:**

- **Prefill, not decode, is the wall on this stack** (item-9/12 findings: ~0.4
  tok/s cold prefill; decode 2.5–5.4 tok/s). Session start is the worst moment —
  the first real turn is cold-prefilling an 18.6 KB system prompt + 11 tools =
  ~9.4K tokens before the first token. The measured **157s session-start stall**
  came from the *full E4B model* running title generation **in parallel**,
  contending for the single Metal GPU during exactly that window.
- A tiny model shrinks the title call's own cost dramatically (270M ≪ E4B), but it
  **does not remove GPU contention** — there is one Metal device, and any second
  inference during the cold-prefill window steals cycles from the first real turn.
  Path 2 reduces the stall; path 1 eliminates the contention entirely.
- Path 1 costs **zero** extra memory, adds **no** second `mlx_lm.server` process
  to start/supervise/health-check, and removes a moving part rather than adding
  one. The only loss is the auto-generated title — low value for a single-user
  local coding loop.

**The case for serving the 270M model (path 2):** keeps the auto-title (a real
convenience for navigating sessions) at negligible memory cost, and the
contention it adds is far smaller than today's E4B-driven stall. Worth it **only
if** the auto-title is deemed valuable enough to pay any session-start latency.

**Recommendation:** measure path 1 first (cheapest, zero-memory, likely the
biggest session-start win). Serve the 270M pick (path 2) only if the auto-title is
judged worth a residual contention cost. Per the item's adopt-if-better rule,
**"disable title-gen"** and **"serve the 270M instruct model"** are both valid
terminal outcomes; this research pins the candidate so the path-1-vs-path-2
latency measurement can decide.

---

## Confirmed findings (verified)

1. **`lmstudio-community/gemma-3-270m-it-MLX-4bit`** — 4-bit MLX quant of
   `gemma-3-270m-it`, 245 MB single-file, at confirmed SHA
   `99d11aebe39a4437f97e21a5216bd1eb3b8f7607` (2025-08-14). *(3-0)*
   — <https://huggingface.co/lmstudio-community/gemma-3-270m-it-MLX-4bit>
2. **`mlx-community/gemma-3-270m-it-4bit`** and **`-it-qat-4bit`** both exist as
   instruct 4-bit MLX repos. *(3-0)*
   — <https://huggingface.co/collections/mlx-community/gemma-3-270m>
3. **`mlx-community/gemma-3-270m-4bit` is the BASE model**, not instruct — wrong
   artifact for this slot. *(3-0)*
   — <https://huggingface.co/mlx-community/gemma-3-270m-4bit>
4. **Gemma 3 270M = 270M params** (170M embedding + 100M transformer, 256k
   vocab) — a genuine sub-1B candidate. *(3-0)*
   — <https://developers.googleblog.com/en/introducing-gemma-3-270m/>
5. **Google ships an instruction-tuned 270M checkpoint** that follows general
   instructions out of the box. *(3-0)*
   — <https://developers.googleblog.com/en/introducing-gemma-3-270m/>
6. **Official QAT INT4 checkpoints exist for Gemma 3 270M** with minimal
   performance degradation. *(2-1)*
   — <https://developers.googleblog.com/en/introducing-gemma-3-270m/>
7. **`mlx-community/gemma-3-1b-it-qat-4bit`** — 4-bit QAT MLX conversion of
   `google/gemma-3-1b-it-qat-q4_0`. *(3-0)*
   — <https://huggingface.co/mlx-community/gemma-3-1b-it-qat-4bit>
8. **Its 4-bit weights total 733 MB** on disk. *(3-0)*
   — <https://huggingface.co/mlx-community/gemma-3-1b-it-qat-4bit>
9. **opencode's `small_model` configures a separate model for lightweight tasks
   like title generation** — the slot this research targets. *(3-0)*
   — <https://opencode.ai/docs/config/>

## Unverified findings (abstained at the session limit — NOT false, re-verify)

- `mlx-community/gemma-3-270m-it-4bit` at SHA
  `ff1143e3a10547c9f2129e94ca37059b096b23f4` (2025-08-14), `model.safetensors`
  = 150,939,130 bytes (~151 MB). *(0-0 abstain)*
- `mlx-community/gemma-3-1b-it-4bit` is a 4-bit MLX quant of `google/gemma-3-1b-it`
  at 733 MB. *(0-0 abstain)*
- `mlx_lm.server` has no `--max-kv-size` bound, so KV cache grows unbounded during
  long sessions — candidate cause of the OOM cliff. *(0-0 abstain)*
- opencode agents' `model` can be overridden via a `provider/model-id` string —
  the mechanism for assigning a local MLX model to the title slot. *(0-0 abstain)*
- If no `small_model` is set, opencode falls back to a cheaper provider model, else
  the main model — i.e. on a single local provider it reuses E4B for titles unless
  a tiny model is configured. *(refuted 0-1 — one refute, two abstain; treat as
  unverified, confirm against opencode source before relying on it.)*

## Sources

Primary: Hugging Face repo cards (`lmstudio-community/gemma-3-270m-it-MLX-4bit`,
`mlx-community/gemma-3-270m-it-4bit`, `mlx-community/gemma-3-270m-4bit`,
`mlx-community/gemma-3-1b-it-4bit`, `mlx-community/gemma-3-1b-it-qat-4bit`,
`google/gemma-3-270m-it`), Google Developers Blog (Gemma 3 270M announcement),
opencode docs (`/docs/config/`, `/docs/agents/`), mlx-lm issues (#883, #1015).
Secondary/blog: DataCamp Gemma-3-270M tutorial; MLX memory-management write-ups
(Hannecke, agileguy.ca, dev.to MLX memory-safety checklist).

---

## Cross-references

- **TODO item 12, task A** (this doc's parent): candidate path 1 (disable
  auto-title) vs path 2 (serve this pick on a second `127.0.0.1` port via
  opencode `small_model`); the path-1-vs-path-2 session-start latency measurement
  decides adoption.
- **`docs/local-model-throughput-research.md`** / **item 9**: the prefill-bound,
  ~40–50K-token Metal-OOM ceiling that frames the memory-budget check (§b).
- **`docs/opencode-local.md`**: the item-8 stack, the pinned E4B main model
  (`mlx-community/gemma-4-E4B-it-qat-4bit`), and the `scripts/mlx.sh` pull path
  (curl-resume + sha256 lfs-oid) used to pin whichever repo §(a) selects.
