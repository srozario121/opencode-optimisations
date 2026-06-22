# Local inference-engine survey — research findings (TODO item 10)

Framework research pass for **TODO item 10** ("Compare SGLang and other inference
engines to MLX for the QAT model"). A broader sequel to item 9: instead of tuning
the MLX path, this surveys **entirely different inference engines** for serving the
local **Gemma 4 E4B QAT** model to opencode on a **16 GB Apple Silicon M1** Mac.

The question it answers: *which engines are even worth standing up and benchmarking
on this hardware* (Metal-native, OpenAI-compatible, can load a 4-bit Gemma 4 E4B,
plausibly fit 16 GB, fully offline at serve time) **vs which are survey-only
(not-applicable) with the disqualifying reason.** That cut gates every downstream
task in item 10.

**Carry-through constraints (from items 8 & 9, non-negotiable).** Fully local /
offline at serve time (no model-hub / package-registry egress once warmed); 16 GB
M1 (Metal, **no CUDA**); single-user interactive opencode workload against an
OpenAI-compatible `127.0.0.1` endpoint; tool/function-calls must stay reliable
(the item-8 repair proxy exists for a reason).

Produced by the `deep-research` harness (2026-06-18): 5 search angles, 19 sources
fetched, 80 claims extracted, 25 put through 3-vote adversarial verification
(23 confirmed, 2 killed). Baseline to beat: **mlx-lm 0.31.3 /
gemma-4-E4B-it-qat-4bit** (numbers in the *Throughput* section of
`docs/opencode-local.md`).

> **Time-sensitivity caveat (dominant).** All tool-calling evidence is an April
> 2026 snapshot and the ecosystem was actively patching Gemma 4 parsers (mlx-lm
> PR #1142; llama.cpp PRs #21326/#21327/#21343). **Re-check tool-call status for
> each candidate at benchmark time** rather than trusting this snapshot. Two
> survey-only verdicts (Ollama MLX backing, TGI CUDA-only) carried 2-1 votes —
> facts held, with the noted scope qualifications. SGLang / TensorRT-LLM /
> ExLlamaV2 N/A rests on the *absence* of any documented Apple-Silicon Metal GPU
> backend rather than one quoted disqualifier (confidence: medium).

---

> **Empirical update (2026-06-19, item-10 benchmark phase).** The candidates below
> were stood up and gate-tested on the actual 16 GB M1; **none beat the
> mlx-lm/E4B baseline** and the MLX default was kept. Headline outcomes:
> **llama.cpp** passed the gate and emits *valid native tool-calls* (the #21316
> bug is fixed in build 9700) but **lost** the agentic metric (reuse TTFT 60 s vs
> 25 s — prefill-bound loop, q4_0 Metal prefill slower than MLX-4bit);
> **vllm-metal** built (after a CommandLineTools libc++ fix) and ran but **lost
> far worse** (reuse TTFT 169 s, degenerate output — batched paged-attention is
> wrong for single-user); **mlx-openai-server** failed the gate (sliding-window
> Gemma can't generate, upstream #312); **SGLang** is N-A (CUDA-only). Also note
> the tool-call ecosystem has moved since this April-2026 snapshot:
> mlx-openai-server now *does* ship a `gemma4` parser, and llama.cpp's is fixed.
> Full per-engine numbers + the toolchain fix: the **Inference engines (item 10)**
> section of `docs/opencode-local.md`.

## TL;DR — the dominant finding

**Throughput is not the gating risk this time — tool-calling is.** Gemma 4's
native tool-call markers (`<|tool_call>…<tool_call|>`) are **mishandled across the
entire Apple-Silicon stack** as of April 2026, not just in our baseline:

- the current **mlx-lm** server returns empty `tool_calls` and leaks raw markup
  into `content` (#1096); its bundled `gemma4.py` parser raises
  `ValueError: No function provided.` on 4-bit Gemma 4 (#1125) — this is exactly
  the bug the repo's repair proxy works around (fix tracked in mlx-lm PR #1142),
- **mlx-openai-server** has **no Gemma tool-call parser at all** (only Qwen / GLM /
  MiniMax / Harmony),
- even **llama.cpp**'s Gemma 4 parser emitted malformed JSON with tokenizer
  artifacts (#21316; related #21680/#21384/#22786 show parse failures / content
  leak persisting after fix PRs),
- **vllm-metal** has no known Gemma tool-call parser either.

So **any benchmark must measure tool-call success rate, not just throughput** — a
prefill/throughput win is worthless to opencode if tool calls don't round-trip.
The repair proxy (or an equivalent) is likely still needed on whichever path wins.

On throughput itself the field splits cleanly: **four engines are Metal-native and
worth a full benchmark** (llama.cpp/llama-server, mlx-openai-server, vllm-metal,
LM Studio); **five are survey-only/N-A** as CUDA-centric (SGLang, vLLM-upstream
core, TGI, TensorRT-LLM, ExLlamaV2); **Ollama** is now MLX-backed but preview-stage
and recommends >32 GB RAM — marginal on a 16 GB M1.

---

## Per-candidate matrix

Legend: ✅ yes · ⚠️ partial/caveated · ❌ no · — unknown/unverified.
"Bench?" = should it get a full harness run on this Mac, or is it survey-only?

| Engine | Metal GPU on M1 | Loads 4-bit Gemma 4 E4B | Prefix/KV reuse | OpenAI API + Gemma tool-calls | Offline serve | 16 GB fit | Bench? |
|---|---|---|---|---|---|---|---|
| **llama.cpp / llama-server** | ✅ first-class | ✅ GGUF Q4 (no *QAT* GGUF confirmed) | ✅ `cache_prompt` (reuse-fail claim **refuted**) | ✅ API; ⚠️ `--jinja` Gemma parser emitted bad JSON (#21316) | ✅ | ✅ likely | **✅ primary** |
| **mlx-openai-server** (cubist38) | ✅ MLX/Metal | ✅ MLX 4-bit (same class as baseline) | ⚠️ scoped prompt-cache + disk cache | ✅ API; ❌ **no Gemma parser** | ✅ | ✅ (E4B class) | **✅ yes** |
| **vllm-metal** (official vLLM plugin) | ✅ MLX/Metal | ⚠️ MLX 4-bit/QAT; Gemma 4 **experimental**, doc'd E2B not E4B | ✅ automatic prefix cache | ✅ API; ❌ no Gemma parser | ✅ | ⚠️ OOM reports on larger variants (#276) | **✅ yes (risky)** |
| **LM Studio** | ✅ MLX + llama.cpp | ✅ either GGUF or MLX 4-bit | ✅ (inherits engine) | ✅ API; ⚠️ inherits mlx-lm/llama.cpp parser bugs | ✅ | ✅ | **⚠️ optional** (GUI app) |
| **Ollama** | ⚠️ MLX preview (0.19) | ✅ GGUF | ✅ | ✅ API; ⚠️ inherits llama.cpp | ✅ | ❌ recommends >32 GB | survey-only |
| **SGLang** | ❌ CUDA-centric | — | — | — | — | — | survey-only / N-A |
| **vLLM (upstream core)** | ❌ CUDA-centric (Metal = the vllm-metal plugin) | — | ✅ (on CUDA) | ⚠️ Gemma 3 pythonic parser | — | — | survey-only / N-A |
| **TGI** | ❌ NVIDIA-only; maintenance mode (2025-12-11) | — | — | — | — | — | survey-only / N-A |
| **TensorRT-LLM** | ❌ NVIDIA-only | — | — | — | — | — | survey-only / N-A |
| **ExLlamaV2** | ❌ CUDA-centric | — | — | — | — | — | survey-only / N-A |

---

## Confirmed findings (verified)

### Metal-native benchmark candidates

1. **llama.cpp / llama-server — the strongest candidate.** Apple Silicon is a
   first-class Metal target ("optimized via ARM NEON, Accelerate and Metal
   frameworks"). Loads GGUF 4-bit and explicitly lists Gemma. `llama-server`
   `cache_prompt` (default **true**) re-uses the common prefix and re-processes
   only the differing suffix — directly addressing item 9's growing-prefix
   limiter. OpenAI-compatible function-calling via `--jinja` (may need
   `--chat-template-file`). **Caveat:** the Gemma 4 tool-call parser emitted
   malformed JSON with tokenizer artifacts (#21316 — `"domain"` rendered as
   `[<|"|>light<|"|>]`); #21680/#21384/#22786 show parse failures / content leak
   persisting after fix PRs → **validate tool-calling empirically**.
   — [llama.cpp](https://github.com/ggml-org/llama.cpp),
   [server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
   [function-calling](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md),
   [#21316](https://github.com/ggml-org/llama.cpp/issues/21316)

2. **mlx-openai-server (cubist38) — same MLX 4-bit class as the baseline.** Runs
   natively on Apple Silicon (MLX/Metal, not CPU fallback), macOS + Python 3.11+.
   Accepts **MLX-format models only** (local path or HF repo), **no GGUF** — a
   4-bit Gemma 4 E4B would reuse the existing MLX weights. Prompt KV caches reused
   only by the generation path that created them; `--prompt-cache-size` +
   disk-backed `--prompt-cache-dir` — favourable for a single-user sequential
   agent loop. **Caveat:** `--tool-call-parser` targets Qwen3 / qwen3_coder /
   glm4_moe / minimax_m2 / harmony — **no Gemma parser** (mirrors the mlx-lm #1096
   gap), so reliable Gemma tool-calls are unproven and likely still need the
   repair proxy.
   — [cubist38/mlx-openai-server](https://github.com/cubist38/mlx-openai-server)

3. **vllm-metal (official `vllm-project` plugin) — NOT the rejected waybarrios
   fork.** "Enables vLLM to run on Apple Silicon Macs using MLX as the primary
   compute backend"; native arm64 Python 3.12; v0.2.0 (April 2026), ~1.3k stars,
   active CI. Gemma 4 is **"🔵 Experimentally supported (GQA + per-layer
   sliding window + YOCO)"** with **automatic prefix cache**; Gemma 3 fully
   supported. Loads 4-bit/QAT MLX checkpoints (doc example
   `mlx-community/gemma-3-1b-it-qat-4bit`) and HF AWQ via mlx-lm's AWQ repack.
   **Caveats:** documented Gemma 4 example is **E2B not E4B**; #281 reports MoE
   expert-routing divergence; #276 reports OOM on larger variants; no Gemma
   tool-call parser found. (Item 9 evaluated and rejected the *waybarrios/vllm-mlx*
   fork — this official plugin is a distinct codebase and is re-evaluated fresh.)
   — [vllm-project/vllm-metal](https://github.com/vllm-project/vllm-metal),
   [supported models](https://docs.vllm.ai/projects/vllm-metal/en/latest/supported_models/)

4. **LM Studio — Metal-native dual engine (Apple MLX + llama.cpp/GGUF).** On Apple
   Silicon it runs LLMs via Apple MLX (Metal GPU) *and* llama.cpp, can mix GGUF and
   MLX models, and serves OpenAI-like endpoints (`/v1/chat/completions`) locally
   and on the network. **Caveat:** because its engines *are* mlx-lm and llama.cpp,
   it **inherits their Gemma 4 tool-call problems and adds no independent fix** —
   and it is a **GUI desktop app**, awkward to drive headless/offline in this
   repo's `scripts/` + `make` style (lower priority than the three CLI servers).
   — [LM Studio docs](https://lmstudio.ai/docs/app),
   [OpenAI compat](https://lmstudio.ai/docs/developer/openai-compat)

### Survey-only / Not-Applicable on a 16 GB M1

5. **SGLang, vLLM-upstream core, TGI, TensorRT-LLM, ExLlamaV2 are CUDA-centric —
   no Apple-Silicon Metal GPU acceleration path**, so they cannot beat the Metal
   baseline. TGI optimized models require NVIDIA H100/A100/A10G/T4 + CUDA 12.2+;
   its backends (CUDA, TensorRT-LLM, llama.cpp-on-CPU, AWS Neuron) include **no
   Metal path**, and TGI entered **maintenance mode 2025-12-11** with HF
   recommending vLLM/SGLang. SGLang / TensorRT-LLM / ExLlamaV2 N-A rests on the
   absence of a documented Metal backend (no single quoted disqualifier → medium
   confidence). **Per item 10, SGLang (the named primary) is still stood up and
   gate-tested** even though it is expected to fail — benchmarked only if it
   passes the Metal-GPU sub-check, otherwise recorded N-A with the failing reason.
   — [TGI NVIDIA install](https://huggingface.co/docs/text-generation-inference/main/en/installation_nvidia),
   [TGI backends](https://github.com/huggingface/text-generation-inference/blob/main/docs/source/multi_backend_support.md)

6. **vLLM upstream supports Gemma 3 tool-calling only via a *pythonic* parser (not
   JSON).** PR #17149 adds a Gemma 3 chat template using
   `--tool-call-parser pythonic`; the model emits `[func(param=value)]` calls
   (later mapped into OpenAI `tool_calls`), not native JSON. The only Metal-viable
   vLLM route on this Mac is the **vllm-metal plugin** (finding 3), not upstream
   core. — [vLLM PR #17149](https://github.com/vllm-project/vllm/pull/17149)

7. **Ollama is now MLX-backed on Apple Silicon but preview-stage and wants
   >32 GB.** "Built on Apple's MLX to use unified memory" (blog, 2026-03-30), but:
   the GPU Neural Accelerator speedup is **M5-exclusive** (an M1 only gets the
   MLX/unified-memory path); it's a **preview (Ollama 0.19)**, full release
   expected Q2 2026; and Ollama **recommends >32 GB unified memory**, which the
   16 GB M1 lacks. Survey-relevant, lower priority than the four primary
   candidates (vote 2-1). — [ollama.com/blog/mlx](https://ollama.com/blog/mlx)

### The cross-engine tool-call risk (verified, high confidence)

8. **Gemma 4 native tool-calling is broken/immature across the entire
   Apple-Silicon stack as of April 2026 — a dominant cross-engine risk, not a
   per-engine quirk.** mlx_lm.server (0.31.x) has no Gemma 4 branch in
   `_infer_tool_parser()` → `tool_calls` stays `[]`, raw markup leaks into
   `content` (#1096); the bundled `gemma4.py` raises `ValueError: No function
   provided.` on 4-bit Gemma 4 (#1125). This is the same bug the repo's repair
   proxy works around (fix tracked in mlx-lm PR #1142). llama.cpp has a Gemma 4
   parser but it produced malformed JSON with tokenizer artifacts (#21316).
   — [mlx-lm #1096](https://github.com/ml-explore/mlx-lm/issues/1096),
   [#1125](https://github.com/ml-explore/mlx-lm/issues/1125),
   [llama.cpp #21316](https://github.com/ggml-org/llama.cpp/issues/21316)

---

## Killed claims (refuted in verification — the negative does NOT hold)

- **"llama.cpp prefix/prompt-cache reuse does NOT work for Gemma 4"** — *refuted*
  (1-2). So llama.cpp prefix reuse most likely **does** work for Gemma 4; treat
  llama.cpp's `cache_prompt`/`--cache-reuse` as a working-reuse candidate and
  confirm at benchmark time. — [#21468](https://github.com/ggml-org/llama.cpp/issues/21468)
- **"The Gemma tool-call template emits a Python list rather than JSON objects"**
  — *refuted* (1-2). — [vLLM PR #17149](https://github.com/vllm-project/vllm/pull/17149)

---

## Open questions (resolve during the benchmark phase)

1. **GGUF availability.** Does a reputable, trustworthy **4-bit/QAT Gemma 4 E4B
   GGUF** exist (Google / Unsloth / ggml-org), or must one be quantized locally?
   No source confirmed a trusted Gemma 4 E4B *QAT* 4-bit GGUF — only generic
   "Gemma" GGUF support and a `Q4_K_M` path. Per item 10's GGUF-acquisition task,
   if no clean source exists the GGUF engines (llama.cpp / LM Studio) drop to
   **survey-only** (this repo runs no bespoke quantization toolchain), and a
   non-QAT Q4 win is reported with an explicit *not-apples-to-apples-quant* note.
2. **mlx-lm PR #1142.** Has it landed and does it fully fix Gemma 4 tool-call
   parsing — letting the repair proxy retire and mlx-openai-server / vllm-metal
   inherit working `tool_calls`? (Cross-references item 8; not resolved here.)
3. **vllm-metal E4B.** Does its experimental Gemma 4 support actually load the
   **E4B** variant (vs only the documented E2B) within 16 GB, and is the MoE
   expert-routing divergence (#281) present for E4B?
4. **The two metrics that gate adoption (item 9).** Per primary candidate, what is
   the measured **tool-call success rate** AND **prefix-cache hit rate** under a
   realistic opencode multi-turn loop on a 16 GB M1?

---

## Recommendation — the benchmark cut

**Stand up + gate-test (then benchmark only if the gate passes), in priority
order:**

1. **llama.cpp / llama-server** — best prior odds: mature Metal backend, working
   prefix reuse (the item-9 limiter), OpenAI API. Gating risk = the Gemma 4
   tool-call parser (#21316) **and** GGUF availability (open question 1). A
   different model *architecture/quant* than the MLX baseline, so a win must carry
   the not-same-quant caveat.
2. **mlx-openai-server** — lowest-friction: reuses the **existing MLX 4-bit E4B
   weights** (same quant as the baseline — a true apples-to-apples comparison),
   adds prompt-cache reuse + KV-quant. Gating risk = **no Gemma tool-call parser**
   (likely still needs the repair proxy).
3. **vllm-metal** — official plugin with automatic prefix cache, loads MLX 4-bit.
   Gating risk = Gemma 4 is **experimental** (E2B-documented), possible OOM/MoE
   issues on E4B in 16 GB; no Gemma tool-call parser.
4. **SGLang** — stood up + gate-tested per item 10's explicit requirement, but
   expected to fail the Metal-GPU sub-check (CUDA-centric) → most likely recorded
   N-A, not benchmarked.

**LM Studio** is **optional** — it adds no engine beyond mlx-lm/llama.cpp and is a
GUI app ill-suited to this repo's headless `scripts/` shape; cover it as
survey/secondary unless a CLI-drivable server mode proves easy.

**Crucial measurement note (carried into the harness phase):** every benchmarked
engine must record **tool-call success rate** alongside the harness's throughput /
agentic-TTFT numbers, run with `MLX_PROXY=0` first to see whether the engine's own
parser suffices. Given finding 8, expect most paths to still require a repair-proxy
equivalent — a throughput win that breaks opencode's edit/shell loop is not an
adoption.

---

## Sources

Primary (GitHub / vendor docs):
[llama.cpp](https://github.com/ggml-org/llama.cpp),
[llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
[llama.cpp function-calling](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md),
[llama.cpp #21316](https://github.com/ggml-org/llama.cpp/issues/21316),
[#21468](https://github.com/ggml-org/llama.cpp/issues/21468),
[cubist38/mlx-openai-server](https://github.com/cubist38/mlx-openai-server),
[vllm-project/vllm-metal](https://github.com/vllm-project/vllm-metal),
[vllm-metal supported models](https://docs.vllm.ai/projects/vllm-metal/en/latest/supported_models/),
[vLLM PR #17149](https://github.com/vllm-project/vllm/pull/17149),
[vLLM automatic prefix caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/),
[mlx-lm #1096](https://github.com/ml-explore/mlx-lm/issues/1096),
[#1125](https://github.com/ml-explore/mlx-lm/issues/1125),
[#980](https://github.com/ml-explore/mlx-lm/issues/980),
[LM Studio docs](https://lmstudio.ai/docs/app),
[LM Studio OpenAI compat](https://lmstudio.ai/docs/developer/openai-compat),
[ollama.com/blog/mlx](https://ollama.com/blog/mlx),
[TGI NVIDIA install](https://huggingface.co/docs/text-generation-inference/main/en/installation_nvidia),
[TGI backends](https://github.com/huggingface/text-generation-inference/blob/main/docs/source/multi_backend_support.md),
[SGLang #19137](https://github.com/sgl-project/sglang/issues/19137).
Secondary (forums/blogs): llama-cpp-python #2227, llama.cpp discussion #13606,
daniel-farina gist.

See also the item-9 research (`docs/local-model-throughput-research.md`) and the
item-9 throughput evaluation (the *Throughput* section of
`docs/opencode-local.md`) — this survey extends them one level up to the
serving-engine choice.
