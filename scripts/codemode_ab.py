#!/usr/bin/env python3
"""End-to-end A/B: flat ReAct vs code-mode round-trips — TODO 21.3 prototype.

21.2a/21.2b established that Gemma-4-E4B CAN emit correct orchestration code (pass@1
1.0 on both tiers, exec and Monty). The only open question left is the ORIGINAL
hypothesis: does collapsing N tool calls into ONE code block actually net faster
**wall-clock** and fewer **decode passes** at 8-12 tok/s, after any repair cost?

This harness measures that directly on the live local model, isolating the round-trip
variable. It runs the SAME multi-step tasks (reused from ``codegen_probe``) two ways:

  * **Arm A — flat ReAct.** One tool call per turn via a strict JSON action protocol:
    the model emits ``{"tool": ..., "args": [...]}``; the harness executes ONE tool and
    replies ``{"result": ...}``; repeat until ``{"final": ...}``. Every turn is a fresh
    decode pass over a GROWING context (prior calls + results) — the current pattern.
  * **Arm B — code-mode.** One code block chaining all calls, executed in the sandbox
    (exec or real Monty), returning only the consolidated result. ~1 decode pass
    (+ optional repair). Tool results never enter the model context.

Primary metric = **decode passes** and **wall-clock** per task (the round-trip
hypothesis). Also: total tokens (prompt+completion summed across turns — flat ReAct
re-prefills the accumulator every turn), pass@1, and failure modes per arm.

This is a CONTROLLED PROXY, not opencode itself: the JSON action loop faithfully
reproduces the one-call-per-decode structure without the native tool-calling plumbing,
so the round-trip comparison is clean. A production 21.3 would wire Arm B as a real
opencode tool; this answers "is it worth doing" first.

Usage:
  scripts/codemode_ab.py selftest                          # offline, scripted model
  scripts/codemode_ab.py run --k 3                          # both arms, default task subset
  scripts/codemode_ab.py run --arms react codemode --tasks sum_lines count_big_files --k 3
  scripts/codemode_ab.py run --engine monty --k 3
  scripts/codemode_ab.py summary

Exit codes: 0 ok · 2 usage/endpoint · 1 run error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegen_probe as cp  # noqa: E402  (sibling — reuse fixture/tools/tasks/grader)

REPO_ROOT = cp.REPO_ROOT
LEDGER = os.path.join(cp.SCRIPTS_DIR, "codemode-ab.jsonl")
# Default to the RAW MLX server (:8081), bypassing the repair proxy (:8080). The proxy's
# gemma4 tool-call parser would convert Arm-A's JSON actions into tool_calls (stripping the
# text content), which is a confound for this text-protocol round-trip measurement. The
# model weights + decode speed are identical either way. Override with --base-url to compare.
BASE_URL = "http://127.0.0.1:8081/v1"

# Multi-step tasks where round-trips dominate make the sharpest A/B; default subset.
DEFAULT_TASKS = ["sum_lines", "count_big_files", "big_balance",
                 "orders_gt10_count", "sum_existing_balances", "longest_file"]

_MISSING = object()


# ---------------------------------------------------------------------------
# Model transport: chat_fn(messages, max_tokens) -> (content, usage_dict)
# ---------------------------------------------------------------------------
def make_http_chat(base_url, model, temperature=0.2, timeout=180.0):
    base = base_url.rstrip("/")

    def chat(messages, max_tokens):
        body = {"model": model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens, "stream": False}
        req = urllib.request.Request(base + "/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            obj = json.loads(r.read().decode())
        msg = obj["choices"][0]["message"]
        content = msg.get("content")
        if not content and msg.get("tool_calls"):  # proxy parsed a native tool call — reconstruct
            fn = msg["tool_calls"][0].get("function", {})
            fa = fn.get("arguments")
            try:
                fa = json.loads(fa) if isinstance(fa, str) else fa
            except json.JSONDecodeError:
                fa = {}
            args = list(fa.values()) if isinstance(fa, dict) else fa
            content = json.dumps({"tool": fn.get("name"), "args": args})
        return content or "", obj.get("usage", {}) or {}

    return chat


def detect_model(base_url, timeout=10.0):
    with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as r:
        data = (json.loads(r.read().decode()).get("data") or [])
    if not data:
        raise RuntimeError("no models at endpoint")
    return data[0]["id"]


# ---------------------------------------------------------------------------
# JSON action parsing (lenient: first balanced {...} object in the text)
# ---------------------------------------------------------------------------
def first_json(text):
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        start = -1
    return None


# ---------------------------------------------------------------------------
# Arm A — flat ReAct (one tool call per decode pass)
# ---------------------------------------------------------------------------
REACT_SYSTEM = (
    "You solve a task by calling tools, ONE per message. Available tools:\n{sigs}\n\n"
    "Respond with EXACTLY ONE JSON object and nothing else, either:\n"
    '  {{"tool": "<name>", "args": [<json-literal args>]}}   to call one tool, or\n'
    '  {{"final": <json value>}}                              when you have the answer.\n'
    "After each tool call I reply with {{\"result\": <value>}}. Pass a prior result as an "
    "arg by copying its literal value. Do NOT write code or call more than one tool per "
    "message. Keep going until you can give {{\"final\": ...}}."
)


def run_react(task, chat, max_turns=12, max_tokens=320):
    call_log = []
    tools = cp._make_tools(call_log)
    exposed = {k: tools[k] for k in task.tools}
    sigs = "\n".join("  " + cp.TOOL_SIGNATURES[t] for t in task.tools)
    messages = [{"role": "system", "content": REACT_SYSTEM.format(sigs=sigs)},
                {"role": "user", "content": task.prompt + "\n\nBegin."}]
    passes = wall = ptoks = ctoks = 0
    final = _MISSING
    terminated = False
    bad_msgs = 0
    for _ in range(max_turns):
        t0 = time.time()
        content, usage = chat(messages, max_tokens)
        wall += time.time() - t0
        passes += 1
        ptoks += usage.get("prompt_tokens", 0)
        ctoks += usage.get("completion_tokens", 0)
        messages.append({"role": "assistant", "content": content})
        obj = first_json(content)
        if obj is None:
            bad_msgs += 1
            messages.append({"role": "user",
                             "content": 'Respond with ONE JSON object only: {"tool":...} or {"final":...}.'})
            continue
        if "final" in obj:
            final = obj["final"]
            terminated = True
            break
        name, args = obj.get("tool"), obj.get("args", [])
        if name in exposed:
            try:
                val = exposed[name](*args) if isinstance(args, list) else exposed[name](args)
            except Exception as e:  # noqa: BLE001
                val = {"error": f"{type(e).__name__}: {e}"}
        else:
            val = {"error": f"unknown tool {name!r}"}
        messages.append({"role": "user", "content": json.dumps({"result": val})})

    expected = task.reference()
    correct = terminated and (final == expected)
    fail = "" if correct else ("no_termination" if not terminated else "wrong_result")
    return {
        "arm": "react", "task": task.id, "passes": passes, "wall_s": round(wall, 2),
        "prompt_tokens": ptoks, "completion_tokens": ctoks, "total_tokens": ptoks + ctoks,
        "tool_calls": len(call_log), "terminated": terminated, "correct": correct,
        "fail": fail, "bad_msgs": bad_msgs,
    }


# ---------------------------------------------------------------------------
# Arm B — code-mode (one code block, sandboxed; optional repair)
# ---------------------------------------------------------------------------
def run_codemode(task, chat, engine, repairs=1, max_tokens=1024):
    messages = [{"role": "system", "content": cp.SYSTEM_PROMPT},
                {"role": "user", "content": cp.build_user_prompt(task)}]
    passes = wall = ptoks = ctoks = 0
    g = None
    for attempt in range(repairs + 1):
        t0 = time.time()
        content, usage = chat(messages, max_tokens)
        wall += time.time() - t0
        passes += 1
        ptoks += usage.get("prompt_tokens", 0)
        ctoks += usage.get("completion_tokens", 0)
        g = cp.grade(task, content, engine, attempt)
        if g.passed or g.fail_stage in ("tools", "orchestration", "correct"):
            break  # a logic error won't be fixed by a generic "it failed" nudge
        if attempt < repairs:  # format/parse/exec error — one repair shot
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user",
                             "content": f"That code failed ({g.error}). Output ONE corrected "
                                        "```python block that assigns the answer to `result`."})
    return {
        "arm": "codemode", "task": task.id, "passes": passes, "wall_s": round(wall, 2),
        "prompt_tokens": ptoks, "completion_tokens": ctoks, "total_tokens": ptoks + ctoks,
        "tool_calls": g.runtime_calls if g else 0, "terminated": True,
        "correct": bool(g and g.passed), "fail": "" if (g and g.passed) else (g.fail_stage if g else "none"),
        "bad_msgs": 0,
    }


ARMS = {"react": run_react, "codemode": run_codemode}


# ---------------------------------------------------------------------------
# Aggregation / reporting
# ---------------------------------------------------------------------------
def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def aggregate(results, arm):
    rs = [r for r in results if r["arm"] == arm]
    if not rs:
        return {}
    n = len(rs)
    return {
        "n": n,
        "pass_at_1": round(sum(r["correct"] for r in rs) / n, 3),
        "mean_passes": _mean([r["passes"] for r in rs]),
        "mean_wall_s": _mean([r["wall_s"] for r in rs]),
        "mean_total_tokens": int(_mean([r["total_tokens"] for r in rs])),
        "fail_modes": _count([r["fail"] for r in rs if r["fail"]]),
    }


def _count(xs):
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def cmd_run(args):
    base_url = args.base_url
    try:
        model = detect_model(base_url)
    except Exception as e:
        print(f"endpoint {base_url} not reachable: {e}", file=sys.stderr)
        return 2
    if "monty" in args.engine:
        try:
            import pydantic_monty  # noqa: F401
        except Exception:
            print("engine 'monty' needs pydantic-monty", file=sys.stderr)
            return 2
    chat = make_http_chat(base_url, model, temperature=args.temperature)
    engine = cp.ENGINES[args.engine]
    task_ids = args.tasks or DEFAULT_TASKS
    tasks = [cp.TASKS_BY_ID[t] for t in task_ids if t in cp.TASKS_BY_ID]

    results = []
    for task in tasks:
        for s in range(args.k):
            for arm in args.arms:
                try:
                    if arm == "react":
                        r = run_react(task, chat, max_turns=args.max_turns)
                    else:
                        r = run_codemode(task, chat, engine, repairs=args.repairs)
                except (urllib.error.URLError, OSError, RuntimeError) as e:
                    print(f"  ! {arm} {task.id} #{s}: {e}", file=sys.stderr)
                    continue
                r["sample"] = s
                results.append(r)
                print(f"  {arm:8s} {task.id:20s} #{s}: {'OK ' if r['correct'] else 'XX '}"
                      f"passes={r['passes']:<2} wall={r['wall_s']:<6} tok={r['total_tokens']:<5} "
                      f"{('fail='+r['fail']) if r['fail'] else ''}")

    summary = {arm: aggregate(results, arm) for arm in args.arms}
    row = {"ts": int(time.time()), "model": model, "engine": args.engine, "k": args.k,
           "tasks": task_ids, "arms": args.arms, "summary": summary, "results": results}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")

    print(f"\n{'arm':10s} {'pass@1':>7s} {'passes':>7s} {'wall_s':>7s} {'tokens':>7s}  fail_modes")
    print("-" * 64)
    for arm in args.arms:
        a = summary.get(arm, {})
        if a:
            print(f"{arm:10s} {a['pass_at_1']:>7} {a['mean_passes']:>7} {a['mean_wall_s']:>7} "
                  f"{a['mean_total_tokens']:>7}  {a['fail_modes']}")
    _readout(summary)
    print(f"\nappended → {LEDGER}")
    return 0


def _readout(summary):
    r, c = summary.get("react"), summary.get("codemode")
    if not (r and c):
        return
    print("\nROUND-TRIP HYPOTHESIS READOUT (codemode vs flat ReAct):")
    for label, key, lower_better in (("decode passes", "mean_passes", True),
                                     ("wall-clock s", "mean_wall_s", True),
                                     ("total tokens", "mean_total_tokens", True),
                                     ("pass@1", "pass_at_1", False)):
        rv, cv = r.get(key, 0), c.get(key, 0)
        if lower_better and rv:
            change = round((rv - cv) / rv * 100, 1)
            print(f"  {label:14s}: react={rv}  codemode={cv}  → codemode {change:+}% "
                  f"({'faster/fewer' if change > 0 else 'slower/more'})")
        else:
            print(f"  {label:14s}: react={rv}  codemode={cv}  → Δ={round(cv - rv, 3):+}")


def cmd_summary(args):
    if not os.path.exists(LEDGER):
        print("no runs yet")
        return 0
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    r = rows[-1]
    print(f"latest run: model={r['model']} engine={r['engine']} k={r['k']} tasks={r['tasks']}")
    print(f"{'arm':10s} {'pass@1':>7s} {'passes':>7s} {'wall_s':>7s} {'tokens':>7s}  fail_modes")
    print("-" * 64)
    for arm in r["arms"]:
        a = r["summary"].get(arm, {})
        if a:
            print(f"{arm:10s} {a['pass_at_1']:>7} {a['mean_passes']:>7} {a['mean_wall_s']:>7} "
                  f"{a['mean_total_tokens']:>7}  {a['fail_modes']}")
    _readout(r["summary"])
    return 0


# ---------------------------------------------------------------------------
# Offline selftest: scripted model proves both loops + grading without the LLM
# ---------------------------------------------------------------------------
def cmd_selftest(args):
    ok = True

    # Scripted ReAct: correct one-call-per-turn sequence for big_balance (ids 1,2,3).
    react_script = [
        '{"tool": "get_user", "args": [1]}',
        '{"tool": "get_user", "args": [2]}',
        '{"tool": "get_user", "args": [3]}',
        '{"final": 2}',
    ]
    it = iter(react_script)

    def scripted_react(messages, max_tokens):
        return next(it), {"prompt_tokens": 100, "completion_tokens": 10}

    r = run_react(cp.TASKS_BY_ID["big_balance"], scripted_react, max_turns=8)
    print(f"  react big_balance: correct={r['correct']} passes={r['passes']} "
          f"tool_calls={r['tool_calls']} (expect correct=True passes=4 tool_calls=3)")
    ok = ok and r["correct"] and r["passes"] == 4 and r["tool_calls"] == 3

    # Scripted ReAct that never terminates -> no_termination failure mode.
    def loop_react(messages, max_tokens):
        return '{"tool": "get_user", "args": [1]}', {"prompt_tokens": 50, "completion_tokens": 8}

    r2 = run_react(cp.TASKS_BY_ID["big_balance"], loop_react, max_turns=5)
    print(f"  react no-term: terminated={r2['terminated']} fail={r2['fail']} (expect no_termination)")
    ok = ok and (r2["fail"] == "no_termination")

    # Scripted code-mode: a correct code block in one shot.
    def scripted_code(messages, max_tokens):
        code = "```python\ntotal = 0\nfor p in list_files('src'):\n    total += count_lines(read_file(p))\nresult = total\n```"
        return code, {"prompt_tokens": 200, "completion_tokens": 40}

    c = run_codemode(cp.TASKS_BY_ID["sum_lines"], scripted_code, cp.ENGINES["exec"], repairs=1)
    print(f"  codemode sum_lines: correct={c['correct']} passes={c['passes']} "
          f"tool_calls={c['tool_calls']} (expect correct=True passes=1)")
    ok = ok and c["correct"] and c["passes"] == 1

    # Scripted code-mode with one bad then good (repair path).
    seq = iter([
        "```python\nresult = undefined_name + 1\n```",                       # exec error
        "```python\nresult = sum(count_lines(read_file(p)) for p in list_files('src'))\n```",
    ])

    def repair_code(messages, max_tokens):
        return next(seq), {"prompt_tokens": 150, "completion_tokens": 30}

    c2 = run_codemode(cp.TASKS_BY_ID["sum_lines"], repair_code, cp.ENGINES["exec"], repairs=1)
    print(f"  codemode repair: correct={c2['correct']} passes={c2['passes']} (expect correct=True passes=2)")
    ok = ok and c2["correct"] and c2["passes"] == 2

    print("\nSELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Flat-ReAct vs code-mode round-trip A/B (TODO 21.3)")
    sub = p.add_subparsers(dest="cmd", required=True)

    rn = sub.add_parser("run", help="run the A/B against the live local model")
    rn.add_argument("--arms", nargs="+", choices=list(ARMS), default=["react", "codemode"])
    rn.add_argument("--tasks", nargs="*", default=None, help="task ids (default: round-trip-heavy subset)")
    rn.add_argument("--k", type=int, default=3, help="samples per task per arm")
    rn.add_argument("--engine", choices=list(cp.ENGINES), default="exec", help="code-mode sandbox")
    rn.add_argument("--repairs", type=int, default=1, help="code-mode repair attempts on a failed run")
    rn.add_argument("--max-turns", type=int, default=12, help="flat-ReAct turn cap")
    rn.add_argument("--temperature", type=float, default=0.2)
    rn.add_argument("--base-url", default=BASE_URL, help="OpenAI-compatible /v1 endpoint")
    rn.set_defaults(func=cmd_run)

    sm = sub.add_parser("summary", help="show the latest A/B run")
    sm.set_defaults(func=cmd_summary)

    st = sub.add_parser("selftest", help="offline scripted-model self-test (no LLM)")
    st.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
