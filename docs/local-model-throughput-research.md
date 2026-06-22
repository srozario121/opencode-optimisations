# Local-model throughput optimisation — research findings (TODO item 9)

Research pass for **TODO item 9** ("Optimise token throughput for local models").
Investigates tooling to raise tokens/sec and lower first-token latency for the
item-8 stack: opencode + Gemma via MLX (`mlx_lm.server`) on a 16 GB M1, fully
local/offline (`HF_HUB_OFFLINE=1`), single-user interactive coding workload,
tool/function-calls must stay reliable.

Produced by the `deep-research` harness (2026-06-16): 5 search angles, 23
sources, 105 extracted claims, 25 put through 3-vote adversarial verification.

> **Verification caveat.** The verification and synthesis stages hit an upstream
> API outage partway through. Of the 25 verified claims, **12 confirmed (3-0)**
> stand. The other 13 are listed by the harness as "refuted" but almost all
> were `0-0 (3 abstain)` — the voters crashed (`ConnectionRefused`) and never
> actually judged them. They are therefore **UNVERIFIED, not false**, and are
> kept below (clearly marked) because several are directly relevant. Re-run the
> research to verify them before relying on them.

---

> **Empirical update (2026-06-17, item 9 evaluation).** The central finding
> below — that mlx-lm's prefix cache is broken for Gemma so the agentic loop
> recomputes the whole prompt every turn — **did NOT reproduce** on the actual
> stack (gemma-4-E4B-it-qat-4bit + mlx-lm 0.31.3). Measured with
> `scripts/mlx_bench.py`, prefix reuse works (cached_tokens covers the prefix;
> only the new chunk is prefilled each turn). The real limiters are slow prefill
> (~160 tok/s), slow decode (~8–12 tok/s), and a ~40–50K Metal-OOM ceiling on
> 16 GB. Neither evaluated approach (vllm-mlx, speculative decoding) was adopted.
> Full results + decision: the **Throughput** section of `docs/opencode-local.md`.

## TL;DR — the dominant finding

**The single biggest throughput problem with the current stack is that
mlx-lm's prefix/KV-cache reuse does not work for Gemma 3.** Gemma 3 uses a 5:1
sliding-window + global-attention pattern, and mlx-lm's prefix cache only works
for *pure full-attention* models — sliding-window/SSM/hybrid models **silently
recompute the entire prompt on every request**. For an agentic coding loop that
resends a large, growing prefix every turn, this is the worst case: a 40K-token
context measured at **~200 s to process vs ~5 s with working cache reuse**.

This means the highest-leverage moves are not micro-tuning the current setup —
they are: **(a)** use a model architecture that gets working prefix reuse
(full-attention, e.g. the already-documented Qwen2.5-Coder fallback), and/or
**(b)** use a serving backend that implements prefix/KV reuse for Gemma. Both
beat any speculative-decoding or quant tweak while the prefix-cache bug stands.

---

## Confirmed findings (3-0 verified)

### Prefix / KV-cache reuse — the throughput killer for Gemma on mlx-lm

1. **mlx-lm prefix cache reuse works only for pure full-attention models.**
   Sliding-window, Mamba/SSM, or hybrid-attention models silently recompute the
   whole prompt every request.
   — [mlx-lm#980](https://github.com/ml-explore/mlx-lm/issues/980)
2. **Gemma 3 is affected** (5:1 sliding-window + global pattern) → prefix/KV
   reuse does **not** function for Gemma-class models in mlx-lm.
   — [mlx-lm#980](https://github.com/ml-explore/mlx-lm/issues/980)
3. **Cost of no reuse: ~200 s vs ~5 s** to process a 40K-token context —
   severe for agentic/coding workloads.
   — [mlx-lm#980](https://github.com/ml-explore/mlx-lm/issues/980)
4. **`mlx_lm.server` does not support the prompt-cache-file feature** at all (it
   exists only for `mlx_lm.generate`), as of April 2026 — so even the manual
   workaround isn't available on the server path we use.
   — [mlx-lm#1178](https://github.com/ml-explore/mlx-lm/issues/1178)
5. A persisted prompt-cache file for a **fixed system prompt** is meant to cut
   first-token latency by pre-computing the cached prefix — relevant for a
   harness that reuses the same system prompt each startup (but see #4: not on
   the server).
   — [mlx-lm#1178](https://github.com/ml-explore/mlx-lm/issues/1178)

### Alternative backends with working cache reuse

6. **`mlx-openai-server` (cubist38)** — OpenAI-compatible MLX server for Apple
   Silicon that supports **KV-cache quantization** (`--kv-bits`, 4 or 8),
   **prompt KV-cache reuse** (`--prompt-cache-size`, default 10), **and**
   speculative decoding (`--draft-model-path`, `--num-draft-tokens`, default 2)
   — though spec-decoding is only on the `lm` path, not the continuous-batch
   path. A potential drop-in replacement for `mlx_lm.server`.
   — [cubist38/mlx-openai-server](https://github.com/cubist38/mlx-openai-server)
7. **LM Studio's updated MLX engine** adds **disk-backed KV-cache
   checkpointing**: saves the cache at 256-token boundaries to disk and restores
   the longest cached prefix for follow-ups, evicting inactive records from
   unified memory while preserving reusable prefixes. (Note: the related "up to
   2× throughput" claim was the *one* genuinely-refuted item — treat the speedup
   number as unproven.)
   — [lmstudio.ai/blog/mlx-engine-agentic-workloads](https://lmstudio.ai/blog/mlx-engine-agentic-workloads)

### Speculative / draft-model decoding

8. **mlx-lm natively supports speculative decoding** via `--draft-model` +
   `--num-draft-tokens`.
   — [mlx-lm#1132](https://github.com/ml-explore/mlx-lm/issues/1132)
9. **But it has correctness bugs**: speculative decoding in mlx-lm 0.30.4 can
   **skip/drop tokens and produce incorrect output** (shown with Qwen3 + small
   draft models)...
   — [mlx-lm#846](https://github.com/ml-explore/mlx-lm/issues/846)
10. ...and the defect is **specific to speculative decoding** — the same model
    and prompt is correct with it disabled. So any spec-decoding adoption needs
    explicit output-correctness validation.
    — [mlx-lm#846](https://github.com/ml-explore/mlx-lm/issues/846)
11. **ReDrafter** (MLX implementation, benchmarked on Apple Silicon Metal GPUs)
    achieves **up to 2.3× speedup** for on-device use — an upper bound on what
    spec-decoding can realistically buy here.
    — [arxiv 2403.09919](https://arxiv.org/pdf/2403.09919)

---

## Unverified but relevant (verification crashed — confirm before trusting)

These are **not fact-checked** (voters abstained on the API outage). Listed
because they materially affect the decision and should be the first things
re-verified.

- **vLLM on Apple Silicon now has two community paths**:
  - **`vllm-metal`** — a vLLM hardware plugin using MLX as the compute backend;
    v0.2.0 (Apr 2026) *claims* 83× TTFT and 3.6× throughput over v0.1.0 via a
    unified paged varlen Metal kernel.
    — [vllm-project/vllm-metal](https://github.com/vllm-project/vllm-metal)
  - **`vllm-mlx`** — vLLM-style server on MLX with continuous batching, paged KV
    cache, **prefix caching**, OpenAI-compatible (+ Anthropic `/v1/messages`)
    API, and **tool-calling with a dedicated Gemma parser** (directly addresses
    both the prefix-cache and reliable-tool-call requirements).
    — [waybarrios/vllm-mlx](https://github.com/waybarrios/vllm-mlx)
- **mlx-lm has `--max-kv-size` (rotating fixed-size KV cache)** to bound KV RAM
  on small-memory machines, and a `mlx_lm.cache_prompt` tool — both unverified.
  — [mlx-lm README](https://github.com/ml-explore/mlx-lm/blob/main/README.md)
- **Q4 KV-cache quantization** reportedly fits ~4× more context into a fixed
  memory budget and (separately) cuts context-restoration TTFT dramatically —
  unverified figures.
  — [arxiv 2603.04428](https://arxiv.org/html/2603.04428v1)
- **MLX vs llama.cpp on Metal** (mixed, unverified): MLX's real decode advantage
  is ~1.4–1.8× (not 3×); llama.cpp/GGUF may **win on prefill** (first-token
  latency) for short prompts; for 4-bit 7B–30B MLX is usually faster overall.
  — [yage.ai](https://yage.ai/share/mlx-apple-silicon-en-20260331.html),
  [hannecke/medium](https://medium.com/@michael.hannecke/llama-cpp-vs-mlx-on-apple-mx-775ee59df0ee)

---

## Recommendations — ranked by gain vs. integration cost

1. **Fix the prefix-cache problem first — it dominates everything else.** Two
   independent levers, do either or both:
   - **Switch the served model to a full-attention architecture** so mlx-lm's
     prefix reuse actually works. The repo already documents **Qwen2.5-Coder**
     as the tool-call fallback; benchmark it as the *throughput* default too.
     Lowest integration cost (config change only), likely the largest real-world
     win for the agentic loop.
   - **Switch the serving backend** to one that does prefix/KV reuse for Gemma:
     **`mlx-openai-server`** (OpenAI-compatible, KV-quant + prompt-cache reuse +
     spec-decoding) or **LM Studio's MLX engine** (disk-backed KV checkpointing).
     Medium cost (swap `scripts/mlx.sh` serve command, re-point opencode), keeps
     Gemma.
2. **Evaluate the vLLM-on-MLX path (`vllm-mlx`)** — *after* re-verifying the
   unverified claims. If real, its prefix caching + Gemma tool-call parser
   target our exact two pain points in one backend. Higher integration risk
   (newer, community project); verify offline operation and 16 GB fit.
3. **KV-cache quantization (`--kv-bits 4/8`)** — cheap memory win that frees
   unified memory for longer context on the 16 GB machine; available in
   `mlx-openai-server` today and reportedly in mlx-lm. Low cost, modest gain.
4. **Speculative decoding — treat as experimental.** Up to ~2.3× *if* it works,
   but mlx-lm has live correctness bugs (token-skipping). Only adopt with an
   output-equivalence check vs. non-speculative, and a draft model small enough
   to coexist in 16 GB. Defer until #1 is settled.
5. **Batching — not relevant.** Continuous/dynamic batching helps concurrent
   serving; single-user interactive use sees little benefit. Skip.

### Suggested next step

Take this to **plan-review** (item 9, task 2) to pick between "swap model to
full-attention" vs "swap backend for Gemma prefix reuse" vs "trial vllm-mlx",
then benchmark the chosen option with before/after tokens/sec + TTFT numbers and
record them in `docs/opencode-local.md`.

---

## Sources

Primary (GitHub issues / repos / arxiv): mlx-lm
[#980](https://github.com/ml-explore/mlx-lm/issues/980),
[#1178](https://github.com/ml-explore/mlx-lm/issues/1178),
[#1132](https://github.com/ml-explore/mlx-lm/issues/1132),
[#846](https://github.com/ml-explore/mlx-lm/issues/846),
[#259](https://github.com/ml-explore/mlx-lm/issues/259);
[cubist38/mlx-openai-server](https://github.com/cubist38/mlx-openai-server);
[vllm-metal](https://github.com/vllm-project/vllm-metal);
[waybarrios/vllm-mlx](https://github.com/waybarrios/vllm-mlx);
[ReDrafter (arxiv 2403.09919)](https://arxiv.org/pdf/2403.09919);
[arxiv 2603.04428](https://arxiv.org/html/2603.04428v1);
[LM Studio MLX engine](https://lmstudio.ai/blog/mlx-engine-agentic-workloads).
Secondary (blogs/forums): yage.ai, hannecke (medium), contracollective,
starmorph, maartengrootendorst, theaiengineer, purplemaia,
[llama.cpp#23752](https://github.com/ggml-org/llama.cpp/issues/23752),
[mlx#3134](https://github.com/ml-explore/mlx/discussions/3134).
