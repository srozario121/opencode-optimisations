# Small-model selection survey **v2** — LATEST-RELEASE 4–9B local coding models vs the Gemma-4-E4B QAT baseline

**TODO item 24.1 deliverable (refresh).** Second deep-research survey, focused on
**current-generation models only** (superseded generations excluded from the ranking) and
**with a release date for every model**. Run 2026-06-27 (`wf_01126133-702`, 95 agents,
3 search angles, 15 sources fetched, 66 claims extracted, 25 verified → 19 confirmed /
6 killed, 7 after-synthesis findings).

> **Supersedes `small-model-selection-research.md` (v1, 2026-06-27).** The field turned
> over generationally since v1: **all four of v1's picks are now superseded or off-budget.**
> Qwen2.5-Coder-7B and Qwen3-4B → superseded by the **Qwen3.5 small series**. v1's
> Gemma-3-4B baseline is correctly excluded — the incumbent is **Gemma 4** (Gemma 4 QAT
> released 2026-06-05, supersedes Gemma 3). Yi-Coder-9B and xLAM-2-3b-fc-r were **not
> re-confirmed** this round (no surviving claim) — their supersession status is unresolved
> (open question).

> **⚠ [lit-only] — this is a RANKING, not an adoption.** Every external number below is
> full-precision / vendor-reported on a **different scaffold** than this stack (4-bit /
> mlx-lm / opencode / repair-proxy / 16 GB M1). Per the repo Evidence policy
> (`[[verify-claims-on-local-harness]]`), **a headline benchmark does NOT transfer**. Only a
> local **K≥3 harness run** (item 24.3) may adopt or reject. This orders *what to A/B first*.

---

## TL;DR verdict (the refreshed shortlist)

Ranked by *current-generation × deployment fit × relevance-to-our-failure-mode*:

| Rank | Candidate | Release date | Params | License | MLX 4-bit build | Fits 16 GB? | Why ranked here |
|---|---|---|---|---|---|---|---|
| **1** | **Qwen3.5-9B** | **2026-03-02** | 9B | Apache-2.0 | `mlx-community/Qwen3.5-9B-MLX-4bit` (~5.6 GB) | ✅ (tight at 40–50K ctx) | Newest in-line Qwen ≤9B; biggest current small model that still fits |
| **2** | **Qwen3.5-4B** | **2026-03-02** | 4B | Apache-2.0 | `mlx-community/Qwen3.5-4B-MLX-4bit` (~2.9–3.0 GB) | ✅ comfortable | Cleanest like-for-like swap (same ~4B size as baseline); direct replacement for v1's Qwen3-4B |
| **3 (baseline)** | **Gemma-4-E4B QAT** *(incumbent)* | **2026-06-05** | 4B-eff | Gemma terms | `mlx-community/gemma-4-qat` (4-bit E4B) | ✅ | Current-gen baseline every candidate is scored against; QAT (not PTQ) quant advantage |
| **4 (weak maybe)** | **Phi-4-Mini** | Phi-4 gen (≈2026-Q1) | 3.8B | MIT | GGUF only confirmed; MLX TBD | ✅ ~2.8 GB Q4 | Only HumanEval 68.3% known — **no** agent/tool-calling data; insufficient to rank highly |

**Flagship/coder variants confirmed OFF-BUDGET (do not A/B locally):**
- **Qwen3-Coder-Next** — 80B-total / 3B-active MoE (released 2026-02-02). MLX 4-bit build is
  **44.8 GB on disk** (~2.8× the 16 GB ceiling). Active-param count does **not** shrink the
  resident weight footprint. Non-viable.
- **Qwen3.6 generation** — ships only as **27B dense** (2026-04-22) + **35B-A3B MoE**
  (2026-04-16). Both >9B / >16 GB. No ≤9B Qwen3.6 variant exists → Qwen3.5 is the newest
  *small* Qwen line.
- **Gemma 4 26B-A4B MoE** (2026-04-02) — ~15 GB at Q4 leaves no KV headroom on a 16 GB M1.
  Prefer E4B. (Note its documented tool-call-to-`reasoning_content` defect below.)

**The decisive caveat (unchanged from v1, now sharper):** **NO external multi-turn
tool-calling benchmark survived adversarial verification for ANY current candidate.** Every
BFCL-on-small-model claim was **refuted** (the ertas.ai Qwen3-4B / Gemma-4-E4B numbers went
0-3 and 1-2; the insiderllm Qwen3.5-9B agent-chain claim went 0-3). Phi-4-Mini's source gave
only HumanEval. So the literature **cannot rank these models on the dimension that matters
most** to this harness (multi-turn tool fidelity) — item 24.3's local run is the *only*
evidence that will exist for it.

---

## Per-candidate detail (verified findings)

### 1. Qwen3.5-9B — top current-gen pick `[confidence: high, 3-0]`

- **Release date: 2026-03-02** (small dense series 0.8B/2B/4B/9B; flagship Qwen3.5 announced
  2026-02-16). Source: official `QwenLM/Qwen3.6` changelog — *"2026-03-02: Qwen3.5-9B,
  Qwen3.5-4B, Qwen3.5-2B, and Qwen3.5-0.8B are now available on Hugging Face Hub."*
- **9B params, Apache-2.0, native multimodal (vision-language), 262K context, thinking mode
  (off by default on the 9B).**
- **Supersedes** both Qwen3-4B and Qwen2.5-Coder-7B from v1 (Qwen3.5 > Qwen3 > Qwen2.5).
- **MLX:** `mlx-community/Qwen3.5-9B-MLX-4bit` exists, disk **~5.6 GB** (the exact-bpw figure
  was *refuted 0-3*, but the ~5.6 GB disk size held 2-1). Fits 16 GB but headroom at the
  40–50K-token ceiling is **tight** — validate in 24.2.
- **⚠ Loader wrinkle:** natively multimodal → the 4-bit build may load via **`mlx_vlm`**, not
  `mlx_lm`. Harness-integration item to confirm before A/B.
- **No verified SWE-bench / Aider / LiveCodeBench / BFCL number** — all refuted or unmeasured.

### 2. Qwen3.5-4B — cleanest like-for-like swap `[high, 3-0 / 2-1]`

- **Release date: 2026-03-02** (same series). 4B params, Apache-2.0, native multimodal.
- **Direct replacement for v1's Qwen3-4B** (now superseded). Same ~4B size class as the
  Gemma baseline → the cleanest single-lever swap.
- **MLX:** `mlx-community/Qwen3.5-4B-MLX-4bit` — 27,539 downloads, last modified 2026-03-02,
  Apache-2.0, **~2.9 GB** (sibling lmstudio-community build 3.03 GB), card states
  *"4-bit (5.347 bits per weight), Group Size: 64"* (note: **5.347 bpw > nominal 4-bit** →
  mixed-precision quant; the effective footprint is a covariate to record). Loads via
  `from mlx_vlm import load, generate` → **same `mlx_vlm` wrinkle** as the 9B.
- Comfortable 16 GB fit with KV/context headroom.

### 3. Gemma-4-E4B QAT — the incumbent baseline (current-gen, NOT superseded) `[high, 3-0]`

- **Release date: 2026-06-05** (Gemma 4 QAT checkpoints: E2B / E4B / 12B). Source: Google
  blog *"Jun 05, 2026 … releasing new checkpoints optimized with Quantization-Aware
  Training (QAT) … Q4_0 … optimize for Apple Silicon with MLX."*
- **Gemma 4 supersedes Gemma 3** → validates excluding Gemma 3 and keeps **E4B as
  latest-in-line**. The baseline is *not* a stale strawman.
- **Quant edge:** ships **official Q4_0 QAT** weights; the MLX 4-bit build
  (`mlx-community/gemma-4-qat`) is **community-converted** (not Google-published). Because
  E4B is QAT and the Qwen candidates are PTQ/AWQ 4-bit, **the baseline carries a quant
  advantage the candidates don't** — a confound to record, not hold constant.
- **⚠ Tool-calling gotcha (shared template family):** Gemma 4's function calling can route
  tool outputs to the **reasoning channel** unless a `--jinja --chat-template-kwargs
  '{"enable_thinking":false}'` fix is applied (corroborated 3-0 for the 26B sibling; same
  template family as E4B). Worth applying/verifying on the E4B serve recipe.

### 4. Phi-4-Mini — weak maybe, insufficient agent data `[medium, 2-1]`

- **Phi-4-generation** model, **3.8B params, MIT license** (full commercial use), ~2.8 GB at
  Q4_K_M. Multiple GGUF quants; **no MLX-specific build confirmed** in this survey.
- **Only HumanEval 68.3%** documented (plus MMLU 73.0, MATH 62.0); source explicitly states
  *"No explicit tool-calling capability metrics are provided."* Microsoft's primary report
  (arXiv 2503.01743) *does* report BFCL, but **no BFCL number was extracted/verified here**.
- Insufficient to rank on the binding agentic dimension. Whether a newer Phi generation
  supersedes it was **not** re-evaluated → treat the release date as approximate (Phi-4 gen).

---

## What the survey could NOT establish (→ resolve in 24.2 / 24.3)

1. **Multi-turn tool-calling scores for ALL candidates are UNMEASURED.** No BFCL v4 /
   SWE-bench Verified / Aider polyglot number for Qwen3.5-9B, Qwen3.5-4B, or Gemma-4-E4B QAT
   survived verification. **This is the binding constraint** and the literature is silent —
   the local shaped-T3 reward + pass/8 (items 17/23) is the only signal that will decide it.
2. **`mlx_lm` vs `mlx_vlm` loader.** Qwen3.5 small models are natively multimodal; their
   4-bit builds may require `mlx_vlm`. Confirm repair-proxy / opencode integration before A/B.
3. **Real resident footprint at 40–50K ctx.** Qwen3.5 builds report **>4-bit effective bpw
   (5.0–5.3)** and carry a **thinking mode** — does enabling thinking blow the OOM budget?
   The 9B's ~5.6 GB weights + KV at the ceiling needs a live fit check.
4. **Supersession of v1's other picks.** Yi-Coder-9B and xLAM-2-3b-fc-r were not
   re-confirmed; newest DeepSeek-Coder / Granite / Codestral / Llama small generations were
   not surfaced. Status unresolved.
5. **Quant-method confound.** Baseline is QAT 4-bit; candidates are PTQ/AWQ 4-bit (and
   mixed-precision >4 bpw). A candidate losing to Gemma might be losing to *quant method*.

---

## Refuted claims (killed in verification — do NOT cite as fact)

| Refuted claim | Vote | Source |
|---|---|---|
| mlx-lm 4-bit Qwen3.5-9B is ~5.059 bpw, group size 64, MLX SafeTensors | 0-3 | mlx-community/Qwen3.5-9B-MLX-4bit |
| Qwen3.6 ships a 27B dense + 35B-A3B MoE with `qwen3_coder` native tool-call parser (the supersession framing) | 1-2 | insiderllm.com |
| Qwen3.5-9B (~6 GB Q4) is the recommended 8 GB-tier FC pick, beats Qwen2.5-7B on agent chains | 0-3 | insiderllm.com |
| Gemma 4 E4B released April 2026, scores mid-to-high 80s BFCL v4, edges Qwen3-4B post-FT | 1-2 | ertas.ai |
| Qwen3-4B-Instruct-2507 is leading sub-7B FC base, high-80s BFCL v4 | 0-3 | ertas.ai |
| Qwen2.5-Coder-7B "still the FIM king, 88.4% HumanEval, not superseded at 7B" | 0-3 | insiderllm.com |

---

## Implications for item 24 (how this shapes 24.2 / 24.3)

- **A/B order (refreshed):** **Qwen3.5-4B** first (cleanest same-size swap, comfortable fit,
  confirmed MLX build) → **Qwen3.5-9B** second (bigger current model, but validate the tight
  40–50K-ctx fit and `mlx_vlm` loader first) → baseline is the recorded **Gemma-4-E4B QAT**.
  **Phi-4-Mini** is an optional stretch arm only if an MLX build materialises in 24.2.
- **Drop from the A/B plan:** v1's Qwen2.5-Coder-7B and Qwen3-4B (superseded). Re-confirm
  Yi-Coder-9B / xLAM status before spending an arm on either.
- **Loader feasibility is now a 24.2 gate item:** the `mlx_vlm`-vs-`mlx_lm` question for the
  Qwen3.5 line is a *build-time* feasibility check — a model that won't serve through the
  repair proxy is a recorded **null arm**, per the Evidence policy.
- **Tool-calling is still the metric to weight, and it's externally blind here.** Keep the
  tool-call-validity floor (`[[item16-lever-sweep-complete]]`) front-and-centre; apply the
  Gemma `enable_thinking=false` jinja fix if E4B routes tool output to the reasoning channel.
- **Quant parity remains a recorded covariate** (QAT baseline vs PTQ/AWQ/mixed candidates),
  not a held constant.

---

## Sources (verified, by quality)

**Primary:**
- Gemma 4 QAT (Google blog, 2026-06-05) — https://blog.google/innovation-and-ai/technology/developers-tools/quantization-aware-training-gemma-4/
- Gemma 4 QAT MLX collection — https://huggingface.co/collections/mlx-community/gemma-4-qat
- Qwen3.6 repo / changelog (Qwen3.5 release dates) — https://github.com/QwenLM/Qwen3.6
- Qwen3-4B-MLX-4bit (official) — https://huggingface.co/Qwen/Qwen3-4B-MLX-4bit
- Qwen3-Coder-Next (80B MoE) — https://huggingface.co/Qwen/Qwen3-Coder-Next
- Qwen3-Coder-Next MLX 4bit (44.8 GB) — https://huggingface.co/lmstudio-community/Qwen3-Coder-Next-MLX-4bit
- arXiv 2603.00729 (Qwen3-Coder-Next details) — https://arxiv.org/html/2603.00729v1
- Phi-4-Mini report — https://arxiv.org/abs/2503.01743

**Secondary (HF model cards / MLX builds):**
- mlx-community/Qwen3.5-4B-MLX-4bit — https://huggingface.co/mlx-community/Qwen3.5-4B-MLX-4bit
- mlx-community/Qwen3.5-9B-MLX-4bit — https://huggingface.co/mlx-community/Qwen3.5-9B-MLX-4bit
- Qwen/Qwen3.5-9B — https://huggingface.co/Qwen/Qwen3.5-9B
- kilo.ai open-source models — https://kilo.ai/open-source-models

**Blog / forum (lower quality; numeric components trace to primary):**
- insiderllm best-local-coding-models-2026 — https://insiderllm.com/guides/best-local-coding-models-2026/
- insiderllm function-calling-local-llms — https://insiderllm.com/guides/function-calling-local-llms/
- ertas.ai on-device tool-calling 2026 — https://www.ertas.ai/blog/on-device-tool-calling-2026-qwen3-gemma4-phi4
- blog.mean.ceo Qwen3.5 small series — https://blog.mean.ceo/qwen-3-5-small-model-series-release/
- lushbinary Qwen3.5 developer guide — https://lushbinary.com/blog/qwen-3-5-developer-guide-benchmarks-architecture-integration-2026/
- localaimaster Phi-4-Mini — https://localaimaster.com/models/phi-4-mini
