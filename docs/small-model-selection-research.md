# Small-model selection survey — 4–7B local coding models vs the Gemma-4-E4B QAT baseline

**TODO item 24.1 deliverable.** Deep-research survey (fan-out web search → source
fetch → 3-vote adversarial verification → synthesis), run 2026-06-27
(`wf_ac51b913-c6b`, 109 agents, 26 sources fetched, 107 claims extracted, 25
verified → 19 confirmed / 6 killed, 6 after-synthesis findings).

> **⚠ [lit-only] — this is a RANKING, not an adoption.** Every external number below is
> full-precision (or vendor-reported) on a **different scaffold** than this stack
> (4-bit / mlx-lm / opencode / repair-proxy / 16 GB M1). Per the repo Evidence policy
> (and `[[verify-claims-on-local-harness]]`), **a headline benchmark score does NOT
> transfer** to "passes on this harness". Only a local **K≥3 harness run** (item 24.3)
> may adopt or reject a candidate. The candidates here are ordered to decide *what to
> A/B first*, nothing more.

---

## TL;DR verdict (the shortlist)

For a fully-local opencode coding agent on a **16 GB M1 / mlx-lm**, ranked by
*external-benchmark strength × deployment fit × relevance-to-our-failure-mode*:

| Rank | Candidate | Why it's on the list | Headline external number | Fits 16 GB @ 4-bit? |
|---|---|---|---|---|
| **1** | **Qwen2.5-Coder-7B-Instruct** | Strongest small code model on every code-edit/code-gen benchmark found; Apache-2.0 | aider edit **57.9%** (top of small field); HumanEval 88.4% | ✅ ~5 GB (Q4) |
| **2** | **Qwen3-4B (Instruct-2507)** | Best *tool-calling*-relevant general model in class; confirmed MLX build exists; same ~4B size as baseline | BFCL **62.04% overall / 35.25% multi-turn** | ✅ confirmed `mlx-community/...-4bit` |
| **3** | **xLAM-2-3b-fc-r** (FC-specialised) | A *function-calling-tuned* small model **beats** larger general models on tool use — the binding constraint for an agent | BFCL **65.74% overall / 55.62% multi-turn** | ✅ (3B) — MLX build TBD (24.2) |
| **4** | **Yi-Coder-9B-Chat** | Next-best general code-editor after Qwen; but 9B is the *upper edge* of the class | aider edit **54.1%** | ⚠ 9B — tighter, validate fit/KV at 40K ctx |
| — | **Gemma-3-4B QAT** *(incumbent baseline)* | A genuinely competitive incumbent, not a strawman | int4 **2.6 GB**, QAT recovers ~54% of 4-bit perplexity drop | ✅ 2.6 GB |

**The decisive framing for item 24:** the binding constraint is **multi-turn
tool-calling**, *not* raw code generation. BFCL evidence is that even SOTA models
reliably handle only **single-turn** calls while **multi-turn / long-horizon** reasoning
"remains an open challenge", and small models collapse exactly there (Qwen3-4B
**35.25%** multi-turn). This matches the repo's own standing finding that the local 0/8
is **capability-bound** (`[[todo-history-and-bottleneck]]`, item 22). So a model with a
high *code-gen* score but weak *multi-turn FC* may not move our wall — which is exactly
why an **FC-specialised model (xLAM-class)** earns a slot despite not being a "coder".

**And the strongest negative result:** there are **NO external SWE-bench Verified
numbers for any 4–9B model** — the leaderboard's smallest open-weight entry is ~27–28B.
Repo-fix capability at this scale is **externally unmeasured**, so item 24.3's local run
isn't just validation, it's the *only* evidence that will exist for this size class.

---

## Per-candidate detail (verified findings)

### 1. Qwen2.5-Coder-7B-Instruct — top code-edit/code-gen pick `[confidence: high, 3-0/2-1]`

- **aider code-editing leaderboard: 57.9%** (whole format, 100% format compliance) —
  **top of the small-model field**, above Yi-Coder-9B (54.1%), Qwen2.5-Coder-3B (39.1%),
  llama-3.1-8b-instruct (37.6%).
- **aider diff-edit: Pass@1 55.6% / Pass@2 68.4%** (Tech Report Table 19).
- **EvalPlus: HumanEval 88.4% / HumanEval+ 84.1% / MBPP 83.5% / MBPP+ 71.7%** (Table 16).
- Apache-2.0; six sizes (0.5/1.5/3/7/14/**32**B); 7B = 7.61B total / 6.53B non-embedding;
  native **32K** context (128K needs YaRN); vendor-claimed SOTA-per-size on
  EvalPlus/LiveCodeBench/BigCodeBench.
- **Fit:** official Q4_K_M GGUF is **4.68 GB** (~5 GB) → comfortable on 16 GB with room
  for a ~40K KV cache.
- **Caveats:** the 57.9% is the **deprecated 133-Python edit** benchmark (NOT aider
  polyglot); aider/HumanEval/MBPP are weak/saturated signals; "SOTA all sizes" / "FIM
  king" are vendor superlatives. **A specific harder-signal claim was REFUTED** (see
  Refuted §): the "18.2% LiveCodeBench / 41.0% BigCodeBench" figures for this model
  **failed verification 0-3** — treat its harder-benchmark standing as *unestablished*.
- Sources: aider edit leaderboard; Qwen2.5-Coder Tech Report (arXiv 2409.12186v3); Qwen
  family blog.

### 2. Qwen3-4B (Instruct-2507) — best in-class tool-caller + confirmed MLX build `[medium, 2-1]`

- **BFCL (prompt-based, no FC fine-tune): 62.04% overall / 75.52% live / 82.58%
  non-live AST / 35.25% multi-turn.** The **35.25% multi-turn** is the number that
  matters for us — it's exactly the agentic regime opencode stresses, and it's low.
- Independently, FISSION-GRPO (arXiv 2601.15625) uses Qwen3-4B as a baseline and lifts
  its multi-turn from ~35% → 40.87% via RL — corroborating the low prompt-based floor.
- **Same ~4B size class as the Gemma baseline** → the cleanest like-for-like swap.
- **MLX build CONFIRMED:** `mlx-community/Qwen3-4B-Instruct-2507-4bit` exists and is
  documented as `mlx_lm.generate --model ...` loadable (source: mlx-community fetch).
  This is the *only* candidate with a directly-confirmed mlx-lm 4-bit checkpoint in this
  survey.
- **Caveat:** the BFCL numbers come from a single Nov-2025 third-party academic table
  (TinyLLM, arXiv 2511.22138), unspecified BFCL version, full-precision — a watch-item.

### 3. xLAM-2-3b-fc-r — FC-specialised, beats larger general models on tools `[medium, 2-1]`

- **BFCL: 65.74% overall / 81.03% live / 88.22% non-live / 55.62% multi-turn** — the
  **strongest small-model result** in the TinyLLM table, **out-scoring the larger general
  Qwen3-4B** on every axis, and notably **55.62% multi-turn** (vs Qwen3-4B's 35.25%).
- **Why it matters:** demonstrates that a **function-calling-tuned** small model can beat
  a general model on the exact skill the opencode harness needs. If our wall is
  tool-call reliability (not codegen), this class is a live hypothesis. 3B → cheapest to
  serve.
- **Caveat:** an FC-tuned model may be *weaker on raw code reasoning* (the 21614-style
  "get-the-fix-right" rung) — it trades codegen for tool fidelity. MLX build availability
  is **TBD in 24.2** (not confirmed in this survey).

### 4. Yi-Coder-9B-Chat — next-best general code-editor `[high, 3-0]`

- **aider code-editing: 54.1%** (whole), directly below Qwen2.5-Coder-7B (57.9%), with
  **no small-model entry in between** — corroborated by the Qwen Tech Report Table 19.
- **Caveat:** **9B is the upper edge** of the 4–7B class; at 4-bit it's ~5–6 GB weights
  but the 40K-token KV cache + the ~40–50K Metal-OOM ceiling on the 16 GB M1
  (`[[item16-lever-sweep-complete]]`) make headroom tight — **fit must be validated in
  24.2**, not assumed.

### Baseline — Gemma-3-4B QAT (the incumbent, NOT a strawman) `[high, 3-0]`

- int4 weights **2.6 GB** (down from 8 GB BF16) → leaves ~13 GB for KV cache.
- **QAT recovers ~54% of the 4-bit perplexity drop** vs naive PTQ (~1.75 PTQ → ~0.8 QAT,
  llama.cpp perplexity). This is the key reason the baseline is competitive: it is a
  *quantization-aware-trained* 4-bit model, so the other candidates (taken at naive
  PTQ/AWQ 4-bit) start with a **quant handicap the baseline doesn't have**.
- **Caveat:** the 54% is a vendor **perplexity** proxy and does **not** directly
  translate to recovered coding/tool-calling accuracy.

---

## What the survey could NOT establish (open questions → resolve in 24.2/24.3)

These are the gaps that make the local run mandatory, not optional:

1. **Per-model tok/s on a 16 GB M1 is UNVERIFIED.** The one sourced throughput claim
   (40–80 tok/s for 3–9B) was **refuted 0-3**. Scattered weaker data points: M1-8GB runs
   Llama-3.1-8B Q4 at only **10–14 tok/s**; an M2-Air-16GB does a 4B Q4 at ~**28 tok/s**.
   The M1 benchmark DB (`mac-llm-bench`) has **no M1 data** ("awaiting contributions").
   → **Measure decode/prefill tok/s locally per candidate** (the repo already cares about
   the ~8–12 tok/s budget, `[[todo-history-and-bottleneck]]`).
2. **4-bit accuracy retention for coding/tool-calling is UNVERIFIED.** The "4-bit retains
   ~98.9% HumanEval+" claim was **refuted 0-3**. The dedicated study *"Smaller = Weaker?
   Benchmarking Robustness of Quantized LLMs in Code Generation"* (arXiv 2506.22776)
   exists and reports smaller models degrade *more* under quant — relevant but not
   quantified per-candidate here.
3. **Does an mlx-lm 4-bit build actually exist for each candidate?** Confirmed only for
   **Qwen3-4B** (`mlx-community/Qwen3-4B-Instruct-2507-4bit`). The claim that **Qwen ships
   `-MLX`-suffixed official checkpoints was REFUTED 0-3** — so MLX availability for
   Qwen2.5-Coder-7B / Yi-Coder-9B / xLAM is an **open practical question for 24.2**
   (mlx-community community builds likely exist but were not verified).
4. **Multi-turn tool-calling *inside opencode* (not BFCL)** is the real test — BFCL
   multi-turn is where all small models collapse, and our harness is all multi-turn.
5. **Is FC-specialisation (xLAM) or grammar/constrained decoding (XGrammar, which mlx-lm
   supports) enough** to beat the QAT-Gemma incumbent on the local repo-fix task?

---

## Refuted claims (killed in verification — do NOT cite as fact)

| Refuted claim | Vote | Source |
|---|---|---|
| Qwen2.5-Coder-7B scores 18.2% LiveCodeBench / 41.0% BigCodeBench | 0-3 | arXiv 2409.12186v3 |
| Qwen ships official `-MLX`-suffixed checkpoints (e.g. `Qwen2.5-7B-Instruct-MLX`) | 0-3 | qwen.readthedocs.io |
| 4-bit retains ~98.9% of full-precision HumanEval+ accuracy | 0-3 | latitude.so |
| M1-16GB runs 3–9B models at ~40–80 tok/s | 0-3 | llmcheck.net |
| LiveCodeBench leaderboard has no 4–9B entries (smallest is 32B) | 0-3 | codesota.com |
| BFCL-V4 "overall accuracy = unweighted average" is the ranking metric | 1-2 | gorilla.cs.berkeley.edu |

---

## Implications for item 24 (how this shapes 24.2/24.3)

- **A/B order:** Qwen2.5-Coder-7B (code strength) and **Qwen3-4B** (confirmed MLX build,
  same size as baseline, tool-call relevance) are the **two highest-value first arms**.
  Add **xLAM-2-3b-fc-r** as the *tool-calling counter-hypothesis* arm — it directly tests
  whether "the wall is tool fidelity, not codegen". Yi-Coder-9B is a stretch arm gated on
  the 24.2 memory-fit check.
- **Quant parity is a real confound:** the baseline is **QAT** 4-bit; the candidates are
  **PTQ/AWQ** 4-bit. That's a covariate to record (per item 24's open design question),
  not a held constant — a candidate losing to Gemma might be losing to *quant method*,
  not architecture.
- **Tool-calling is the metric to weight:** keep the harness's tool-call-validity floor
  (`[[item16-lever-sweep-complete]]`) front-and-centre; a candidate with a great aider
  score but BFCL multi-turn in the 30s may regress the floor the way item 18's terse
  prompt did. Grammar/constrained decoding via mlx-lm (XGrammar) is a cheap lever to pair
  with any candidate that emits malformed tool-calls.
- **No external repo-fix ground truth exists at this scale** → the local shaped-T3 reward
  + full pass/8 (items 17/23) is the *only* signal that will decide this. Reuse it.

---

## Sources (verified, by quality)

**Primary:**
- aider code-editing leaderboard — https://aider.chat/docs/leaderboards/edit.html
- aider Qwen3 polyglot results — https://aider.chat/2025/05/08/qwen3.html
- Qwen2.5-Coder Technical Report — https://arxiv.org/html/2409.12186v3
- Qwen2.5-Coder family blog — https://qwenlm.github.io/blog/qwen2.5-coder-family/
- BFCL (Patil et al., PMLR v267, 2025) — https://proceedings.mlr.press/v267/patil25a.html
- Berkeley Function-Calling Leaderboard — https://gorilla.cs.berkeley.edu/leaderboard.html
- TinyLLM (BFCL small-model table, Nov 2025) — https://arxiv.org/pdf/2511.22138
- "Small Models, Big Tasks" (SLM tool-calling, Apr 2025) — https://arxiv.org/pdf/2504.19277
- LiveCodeBench paper — https://arxiv.org/pdf/2403.07974
- OpenCoder paper — https://arxiv.org/html/2411.04905v1
- "Smaller = Weaker?" quant-robustness on codegen — https://arxiv.org/pdf/2506.22776
- Gemma 3 QAT (Google Developers Blog) — https://developers.googleblog.com/en/gemma-3-quantized-aware-trained-state-of-the-art-ai-to-consumer-gpus/
- mlx-community (HF org, MLX builds) — https://huggingface.co/mlx-community
- SWE-bench Verified leaderboard — https://llm-stats.com/benchmarks/swe-bench-verified

**Secondary / blog / forum (numeric components trace to primary; treat as lower quality):**
- insiderllm best-local-coding-models — https://insiderllm.com/guides/best-local-coding-models-2026/
- latitude.so quantized-LLM cost/perf — https://latitude.so/blog/quantized-llms-cost-performance-results
- llmcheck.net Apple-Silicon benchmarks — https://llmcheck.net/benchmarks
- mac-llm-bench — https://github.com/enescingoz/mac-llm-bench
- Rapid-MLX throughput — https://github.com/raullenchai/Rapid-MLX
- localaimaster local coding models — https://localaimaster.com/models/best-local-ai-coding-models
