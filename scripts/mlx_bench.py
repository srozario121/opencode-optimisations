#!/usr/bin/env python3
"""Throughput benchmark harness for the local coding-agent stack (TODO item 9).

Drives any OpenAI-compatible ``/v1`` endpoint on ``127.0.0.1`` (mlx-lm.server,
the repair proxy, vllm-mlx, mlx-openai-server, …) and records three metrics so
serving configurations can be compared on an identical workload:

  * single-shot TTFT       — time to first streamed token on a small prompt
  * single-shot decode t/s — output tokens / decode time on that prompt
  * multi-turn agentic TTFT — THE HEADLINE METRIC. Simulates the opencode loop:
                              a large context that GROWS each turn and is resent
                              in full every turn. With working prefix/KV reuse,
                              per-turn TTFT stays low; without it (the Gemma /
                              mlx-lm sliding-window bug, research finding #1-3),
                              TTFT climbs with context as the whole prompt is
                              re-prefilled every turn. This is the number that
                              decides item 9.

Stdlib only (urllib/json/argparse) — the sanctioned non-service ``scripts/``
shape: no project dependency, nothing imported by ``src/``. Reads/writes a
results JSONL so configs measured on different days stay comparable; ``--summary``
prints the comparison table without running anything.

The workload is fully deterministic (fixed synthetic "codebase" context, fixed
prompts, fixed turn count) so every backend is measured identically — see
``build_context`` / ``AGENTIC_TURNS`` / the defaults below. Document any change
to those in docs/opencode-local.md so old result rows stay comparable.

Usage:
  scripts/mlx_bench.py --label "mlx-lm/E4B baseline"      # run + append a row
  scripts/mlx_bench.py --label vllm-mlx --base-url http://127.0.0.1:8080/v1
  scripts/mlx_bench.py --quick --label smoke              # fast harness check
  scripts/mlx_bench.py --summary                          # print comparison table

Exit codes: 0 ok · 2 usage/config (endpoint unreachable, no model) · 1 run error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_OUT = os.path.expanduser("~/.config/opencode-optimisations/mlx-bench/results.jsonl")

# Chars/token ratio used to SIZE the synthetic context without a tokenizer
# (stdlib-only). Calibrated against this dense synthetic-code blob: 32000 chars
# measured at ~14300 tokens on the Gemma tokenizer => ~2.3. The server-reported
# prompt_tokens (when the backend returns usage) is the authoritative count;
# this estimate only drives how much text we generate so ctx targets land near
# their nominal token sizes.
CHARS_PER_TOKEN = 2.3

# Agentic loop shape (deterministic; keep stable so rows stay comparable).
# Faithful to opencode: a large codebase context is loaded up front (turn 1),
# then each turn adds a modest chunk and resends everything. Sized to top out
# at ~28K actual tokens by turn 5 — safely below the ~40-50K Metal-OOM cliff for
# E4B on a 16 GB M1 (a full ramp to 40K crashes the mlx-lm server; see
# docs/opencode-local.md). Raise on bigger hardware via the CLI flags.
DEFAULT_TURNS = 5          # turn 5 reaches ~ctx_base + 4*ctx_step tokens
DEFAULT_CTX_BASE = 12000   # tokens of context loaded in turn 1
DEFAULT_CTX_STEP = 4000    # tokens of fresh context appended each later turn
DEFAULT_MAX_TOKENS = 120   # output cap per generation (keep decode bounded)


def _gen_blob(approx_tokens: int, seed: int) -> str:
    """Deterministic synthetic 'source file' of ~approx_tokens tokens.

    Numbered helper functions with varied identifiers — token-dense like real
    code, reproducible for a given (approx_tokens, seed), and distinct per seed
    so successive turns append genuinely new content (a real growing prefix).
    """
    target_chars = approx_tokens * CHARS_PER_TOKEN
    lines: list[str] = [f"# synthetic module block seed={seed}"]
    n = 0
    size = len(lines[0])
    while size < target_chars:
        i = seed * 100003 + n
        line = (
            f"def op_{seed}_{n}(x_{n}, y_{n}, acc={i % 97}):\n"
            f"    # block {seed}.{n}: fold inputs into a running accumulator\n"
            f"    total = acc + x_{n} * {i % 31} - y_{n} % {1 + i % 13}\n"
            f"    return total if total > {i % 257} else -total\n"
        )
        lines.append(line)
        size += len(line)
        n += 1
    return "\n".join(lines)


# Fixed agentic questions asked turn-by-turn (cheap to answer, force a real
# generation but keep output bounded so TTFT dominates the measurement).
AGENTIC_TURNS = [
    "Briefly: in one sentence, what do these functions have in common?",
    "Name one function from the code above and say what it returns, in one line.",
    "In one sentence, is there any obvious bug pattern here?",
    "One sentence: how would you unit-test one of these functions?",
    "One word: are these functions pure (no side effects)? Answer yes or no.",
    "One sentence: summarise what this module does overall.",
    "One line: which function name appears last in the code above?",
    "One sentence: suggest a better name for any one of these functions.",
]

SINGLE_SHOT_PROMPT = (
    "Write a short Python function `is_prime(n)` that returns True if n is "
    "prime. Include a one-line docstring. Output only the code."
)


@dataclass
class TurnResult:
    turn: int
    est_prompt_tokens: int
    server_prompt_tokens: int | None
    server_cached_tokens: int | None  # prompt_tokens_details.cached_tokens — prefix-reuse evidence
    ttft_s: float
    output_tokens: int
    decode_tps: float | None
    wall_s: float


@dataclass
class BenchResult:
    label: str
    model: str
    base_url: str
    timestamp: str
    params: dict
    single_ttft_s: float | None = None
    single_decode_tps: float | None = None
    single_output_tokens: int | None = None
    single_streamed: bool | None = None
    agentic_turns: list[TurnResult] = field(default_factory=list)
    agentic_turn1_ttft_s: float | None = None
    agentic_reuse_ttft_s: float | None = None  # mean TTFT of turns >= 2 (headline)
    agentic_total_wall_s: float | None = None
    agentic_last_prompt_tokens: int | None = None  # server prompt_tokens, last turn
    agentic_last_cached_tokens: int | None = None  # server cached_tokens, last turn
    error: str | None = None


def _post_stream(base_url: str, payload: dict, timeout: float):
    """POST /chat/completions with stream=True. Yields (event_type, data).

    event_type is "first" for the first content token (carries ttft via closure
    timing in the caller), "delta" for each content chunk, "usage" for the final
    usage object, "done" at end. Raises on transport errors.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                yield ("done", None)
                return
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                # Gemma 4 QAT streams thinking tokens before the final answer:
                # mlx-lm uses `reasoning`, vllm-mlx uses `reasoning_content`.
                # All are generated tokens, so any of them counts for TTFT and
                # decode-rate timing.
                token = (delta.get("content") or delta.get("reasoning")
                         or delta.get("reasoning_content"))
                if token:
                    yield ("delta", token)
            if obj.get("usage"):
                yield ("usage", obj["usage"])
        yield ("done", None)


@dataclass
class GenMetrics:
    ttft_s: float
    output_tokens: int
    decode_tps: float | None  # None when the endpoint buffered (no real stream)
    server_prompt_tokens: int | None
    server_completion_tokens: int | None
    server_cached_tokens: int | None
    streamed: bool


def _run_generation(base_url: str, messages: list[dict], max_tokens: int,
                    timeout: float, model: str) -> GenMetrics:
    """One streamed generation, measured token-by-token.

    decode_tps is computed over the first->last content-chunk span (the true
    decode window), NOT wall-minus-ttft. If a backend BUFFERS the whole response
    (e.g. the tool-call repair proxy, which must accumulate to repair), all
    chunks land at once: span ~ 0, streamed=False, decode_tps=None — so the row
    flags "not measurable here" instead of reporting a garbage rate. Throughput
    is therefore measured against the backend directly, not through the proxy.

    server_cached_tokens (prompt_tokens_details.cached_tokens) is the backend's
    own report of how much of the prompt it served from cache — direct evidence
    of prefix reuse across turns.
    """
    payload = {
        "model": model,  # required by vllm-mlx (mlx-lm tolerates its absence)
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
        # Ask backends that support it for a final usage chunk; harmless if not.
        "stream_options": {"include_usage": True},
    }
    start = time.perf_counter()
    first_t: float | None = None
    last_t: float | None = None
    chunks = 0
    sp = sc = cached = None
    for kind, data in _post_stream(base_url, payload, timeout):
        if kind == "delta":
            now = time.perf_counter()
            if first_t is None:
                first_t = now
            last_t = now
            chunks += 1
        elif kind == "usage":
            sp = data.get("prompt_tokens")
            sc = data.get("completion_tokens")
            details = data.get("prompt_tokens_details") or {}
            cached = details.get("cached_tokens")
    wall = time.perf_counter() - start
    if first_t is None:
        # No content streamed at all — surface as full-wall TTFT, no decode rate.
        return GenMetrics(round(wall, 3), 0, None, sp, sc, cached, False)
    ttft = first_t - start
    out_tokens = sc if sc else chunks
    span = (last_t - first_t) if last_t is not None else 0.0
    if span < 0.05 and out_tokens > 2:
        # Buffered delivery (e.g. via the repair proxy): rate not measurable.
        return GenMetrics(round(ttft, 3), out_tokens, None, sp, sc, cached, False)
    decode_tps = (out_tokens - 1) / span if span > 0 else None
    return GenMetrics(round(ttft, 3), out_tokens,
                      round(decode_tps, 2) if decode_tps is not None else None,
                      sp, sc, cached, True)


def detect_model(base_url: str, timeout: float = 10.0) -> str:
    url = base_url.rstrip("/") + "/models"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        obj = json.loads(resp.read().decode())
    data = obj.get("data") or []
    if not data:
        raise RuntimeError("GET /v1/models returned no models")
    return data[0].get("id", "unknown")


def run_single_shot(base_url: str, max_tokens: int, timeout: float,
                    result: BenchResult) -> None:
    msgs = [{"role": "user", "content": SINGLE_SHOT_PROMPT}]
    m = _run_generation(base_url, msgs, max_tokens, timeout, result.model)
    result.single_ttft_s = m.ttft_s
    result.single_decode_tps = m.decode_tps
    result.single_output_tokens = m.output_tokens
    result.single_streamed = m.streamed


def run_agentic(base_url: str, turns: int, ctx_base: int, ctx_step: int,
                max_tokens: int, timeout: float, result: BenchResult) -> None:
    """Growing-prefix multi-turn loop — the headline measurement.

    Turn 1 sends a system msg + a large context block + a question. Each later
    turn appends the model's actual reply, then a NEW context block + the next
    question, and resends the whole conversation. The shared prefix across
    consecutive turns is everything before the freshly-appended block, so a
    backend with working prefix reuse only prefills the new block (cheap, flat
    TTFT); a backend without it re-prefills the whole growing prompt (TTFT
    climbs). That divergence is exactly what item 9 needs to see.
    """
    messages: list[dict] = [
        {"role": "system",
         "content": "You are a terse code assistant. Answer in the requested length."},
    ]
    est_tokens = 0
    loop_start = time.perf_counter()
    turn_results: list[TurnResult] = []
    for t in range(turns):
        question = AGENTIC_TURNS[t % len(AGENTIC_TURNS)]
        if t == 0:
            blob = _gen_blob(ctx_base, seed=t)
            est_tokens += ctx_base
            content = f"Here is part of a codebase:\n\n{blob}\n\n{question}"
        else:
            blob = _gen_blob(ctx_step, seed=t)
            est_tokens += ctx_step
            content = f"Here is more of the codebase:\n\n{blob}\n\n{question}"
        messages.append({"role": "user", "content": content})

        t0 = time.perf_counter()
        m = _run_generation(base_url, messages, max_tokens, timeout, result.model)
        wall = time.perf_counter() - t0
        turn_results.append(TurnResult(
            turn=t + 1, est_prompt_tokens=est_tokens,
            server_prompt_tokens=m.server_prompt_tokens,
            server_cached_tokens=m.server_cached_tokens,
            ttft_s=m.ttft_s, output_tokens=m.output_tokens,
            decode_tps=m.decode_tps, wall_s=round(wall, 3)))
        tps_str = f"{m.decode_tps:6.1f}" if m.decode_tps is not None else "  buf "
        cached_str = (f" cached={m.server_cached_tokens}"
                      if m.server_cached_tokens is not None else "")
        print(f"  turn {t + 1}/{turns}: ctx~{est_tokens:>6} tok  "
              f"TTFT={m.ttft_s:6.2f}s  out={m.output_tokens:>3}  "
              f"decode={tps_str} t/s  wall={wall:6.2f}s{cached_str}", flush=True)

        # Append a synthetic assistant reply so the prefix grows realistically
        # without depending on what the model actually said this run.
        messages.append({"role": "assistant",
                         "content": f"(answer to turn {t + 1})"})

    result.agentic_turns = turn_results
    result.agentic_turn1_ttft_s = turn_results[0].ttft_s
    later = [tr.ttft_s for tr in turn_results[1:]]
    result.agentic_reuse_ttft_s = round(sum(later) / len(later), 3) if later else None
    result.agentic_total_wall_s = round(time.perf_counter() - loop_start, 3)
    result.agentic_last_prompt_tokens = turn_results[-1].server_prompt_tokens
    result.agentic_last_cached_tokens = turn_results[-1].server_cached_tokens


def _now_iso() -> str:
    # Wall-clock stamp for the result row (this is a normal CLI script, not a
    # workflow; time.* is available).
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def append_result(out_path: str, result: BenchResult) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    row = asdict(result)
    with open(out_path, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"\nAppended result -> {out_path}")


def print_summary(out_path: str) -> None:
    if not os.path.exists(out_path):
        print(f"No results yet at {out_path}")
        return
    rows = []
    with open(out_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        print(f"No results in {out_path}")
        return
    hdr = (f"{'label':<26} {'when':<17} {'1shot TTFT':>10} {'1shot t/s':>10} "
           f"{'turn1 TTFT':>11} {'reuse TTFT':>11} {'agentic wall':>13} "
           f"{'cache@last':>14}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        def fmt(v, suf=""):
            return (f"{v}{suf}" if v is not None else "-")
        cached = r.get("agentic_last_cached_tokens")
        prompt = r.get("agentic_last_prompt_tokens")
        cache_cell = f"{cached}/{prompt}" if cached is not None and prompt else "-"
        print(f"{r.get('label', '?'):<26} {r.get('timestamp', '?'):<17} "
              f"{fmt(r.get('single_ttft_s'), 's'):>10} "
              f"{fmt(r.get('single_decode_tps')):>10} "
              f"{fmt(r.get('agentic_turn1_ttft_s'), 's'):>11} "
              f"{fmt(r.get('agentic_reuse_ttft_s'), 's'):>11} "
              f"{fmt(r.get('agentic_total_wall_s'), 's'):>13} "
              f"{cache_cell:>14}")
    print("\nHeadline = 'reuse TTFT' (mean TTFT of turns >= 2): lower is better; "
          "flat vs turn1 means prefix reuse is working.")
    print("cache@last = server cached_tokens / prompt_tokens on the final turn; "
          "cached≈prompt means the growing prefix is being reused.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--model", default=None,
                   help="model id (default: auto-detect via GET /v1/models)")
    p.add_argument("--label", default=None,
                   help="config label for the results row (required unless --summary)")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--turns", type=int, default=DEFAULT_TURNS)
    p.add_argument("--ctx-base", type=int, default=DEFAULT_CTX_BASE)
    p.add_argument("--ctx-step", type=int, default=DEFAULT_CTX_STEP)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--timeout", type=float, default=600.0,
                   help="per-request socket timeout (s); 40K no-cache prefill is slow")
    p.add_argument("--no-single", action="store_true", help="skip single-shot test")
    p.add_argument("--no-agentic", action="store_true", help="skip agentic test")
    p.add_argument("--quick", action="store_true",
                   help="fast harness check: small ctx, 3 turns")
    p.add_argument("--summary", action="store_true",
                   help="print the comparison table from --out and exit")
    args = p.parse_args(argv)

    if args.summary:
        print_summary(args.out)
        return 0

    if not args.label:
        print("error: --label is required (or use --summary)", file=sys.stderr)
        return 2

    if args.quick:
        args.turns = 3
        args.ctx_base = 1500
        args.ctx_step = 1500
        args.max_tokens = 40

    try:
        model = args.model or detect_model(args.base_url)
    except (urllib.error.URLError, OSError) as e:
        print(f"error: cannot reach {args.base_url} ({e}). Is the server up "
              f"('make mlx-up')?", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"error: model detection failed: {e}", file=sys.stderr)
        return 2

    result = BenchResult(
        label=args.label, model=model, base_url=args.base_url,
        timestamp=_now_iso(),
        params={"turns": args.turns, "ctx_base": args.ctx_base,
                "ctx_step": args.ctx_step, "max_tokens": args.max_tokens,
                "chars_per_token": CHARS_PER_TOKEN},
    )
    print(f"Benchmarking '{args.label}'  model={model}  url={args.base_url}")
    print(f"  params: turns={args.turns} ctx_base={args.ctx_base} "
          f"ctx_step={args.ctx_step} max_tokens={args.max_tokens}\n")

    try:
        if not args.no_single:
            print("single-shot:")
            run_single_shot(args.base_url, args.max_tokens, args.timeout, result)
            print(f"  TTFT={result.single_ttft_s}s  "
                  f"decode={result.single_decode_tps} t/s  "
                  f"out={result.single_output_tokens}\n")
        if not args.no_agentic:
            print("agentic (growing-prefix multi-turn):")
            run_agentic(args.base_url, args.turns, args.ctx_base, args.ctx_step,
                        args.max_tokens, args.timeout, result)
            print(f"\n  turn1 TTFT={result.agentic_turn1_ttft_s}s  "
                  f"reuse TTFT (turns>=2 mean)={result.agentic_reuse_ttft_s}s  "
                  f"total wall={result.agentic_total_wall_s}s")
    except KeyboardInterrupt:
        result.error = "interrupted"
        print("\ninterrupted", file=sys.stderr)
    except (urllib.error.URLError, OSError) as e:
        result.error = f"transport: {e}"
        print(f"\nerror during run: {e}", file=sys.stderr)
        append_result(args.out, result)
        return 1

    append_result(args.out, result)
    print()
    print_summary(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
