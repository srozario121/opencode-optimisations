#!/usr/bin/env python3
"""Production A/B: baseline opencode vs codemode-enabled opencode — TODO 21.4b.

21.3 measured the round-trip win with a MOCK-tool proxy harness (code-mode beat flat
ReAct by ~5x wall-clock, −91% tokens, +0.667 pass@1, ReAct non-terminating on 4/6
tasks). 21.4a wired code-mode into REAL opencode (`.opencode/tools/codemode.ts` +
`scripts/codemode_exec.py`) and confirmed the local model invokes it natively. This is
the final measurement: does the win survive in REAL opencode with REAL files and REAL
tool latency?

It runs the SAME multi-file tasks two ways against the frozen `harness_micro_fixtures`
repo, reusing the verified `harness_micro` machinery (isolated-config materialization,
`opencode run --format json`, tool-call transcript parsing):

  * **baseline** — the cloned global config (read/grep/glob/list/edit); the model does
    one tool call per operation, returning to the loop each time.
  * **codemode** — same config PLUS the `codemode` tool installed and a one-line rule
    nudging its use for multi-file work; the model batches operations into one call.

Per-arm metrics: tool calls (decode round-trips), wall-clock, status (ok/timeout), and
best-effort correctness (expected answer present in the transcript). The headline is the
codemode-vs-baseline delta on calls + wall-clock, and whether baseline churns/times out
where codemode does not.

Usage:
  scripts/codemode_prod_ab.py run --k 1                       # both arms, all tasks
  scripts/codemode_prod_ab.py run --tasks count_lines --arms baseline codemode --k 1
  scripts/codemode_prod_ab.py summary
  scripts/codemode_prod_ab.py selftest                        # offline (no opencode/model)

Exit codes: 0 ok · 2 usage/endpoint · 1 run error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_micro as hm  # noqa: E402  (reuse config/episode/transcript machinery)
import harness_eval as he  # noqa: E402

REPO_ROOT = hm.REPO_ROOT
LEDGER = os.path.join(REPO_ROOT, "scripts", "codemode-prod-ab.jsonl")
CODEMODE_TS = os.path.join(REPO_ROOT, ".opencode", "tools", "codemode.ts")
CODEMODE_EXEC = os.path.join(REPO_ROOT, "scripts", "codemode_exec.py")
VENV_PY = os.path.join(REPO_ROOT, ".venv", "bin", "python")

CODEMODE_NUDGE = (
    "# Tool guidance\n\n"
    "When a task needs you to read, scan, count, or aggregate across MULTIPLE files, "
    "use the `codemode` tool: write ONE small Python program that calls the host-tools "
    "(read_file, read_lines, list_files, glob, grep) and assigns the answer to `result`. "
    "Prefer ONE codemode call over many separate read/grep calls — it is much faster.\n"
)


# ---------------------------------------------------------------------------
# Tasks — multi-file, round-trip-heavy, expected answer computed from the fixture.
# ---------------------------------------------------------------------------
def _fx(path):
    return os.path.join(hm.fixture_root(), path)


def _exp_count_lines():
    return str(sum(len(open(_fx(f)).read().splitlines())
                   for f in ("calc.py", "helpers.py", "utils.py")))


def _exp_def_count():
    n = 0
    for f in ("calc.py", "store.py", "helpers.py", "utils.py"):
        n += sum(1 for ln in open(_fx(f)) if ln.lstrip().startswith("def "))
    return str(n)


def _exp_find_clamp():
    for f in ("calc.py", "store.py", "helpers.py"):
        if re.search(r"def\s+clamp\b", open(_fx(f)).read()):
            return f
    return "none"


TASKS = {
    "count_lines": {
        "prompt": ("Count the TOTAL number of lines across the three files calc.py, "
                   "helpers.py and utils.py in this project. Report just the integer total."),
        "expected": _exp_count_lines,
    },
    "def_count": {
        "prompt": ("How many top-level `def ` function definitions are there in TOTAL across "
                   "calc.py, store.py, helpers.py and utils.py? Report just the integer."),
        "expected": _exp_def_count,
    },
    "find_clamp": {
        "prompt": ("Which one of these files defines a function named `clamp`: calc.py, "
                   "store.py, or helpers.py? Report just the filename."),
        "expected": _exp_find_clamp,
    },
}


# ---------------------------------------------------------------------------
# Arm config materialization
# ---------------------------------------------------------------------------
def _base_cfg():
    return hm.load_micro_config("micro-baseline")


def build_arm(arm: str, xdg_root: str, model_ref: str, workdir: str):
    """Materialize an isolated opencode config for the arm; return (cfg_dir, env)."""
    cfg = _base_cfg()
    cfg_dir = hm.build_config_dir(cfg, xdg_root, model_ref, trace=False)
    env = hm.episode_env(cfg, xdg_root)
    if arm == "codemode":
        # install the codemode tool into the isolated config's tools dir
        tools_dst = os.path.join(cfg_dir, "tools")
        os.makedirs(tools_dst, exist_ok=True)
        shutil.copy2(CODEMODE_TS, os.path.join(tools_dst, "codemode.ts"))
        # nudge rule + register it in instructions
        nudge_path = os.path.join(cfg_dir, "codemode-nudge.md")
        with open(nudge_path, "w") as f:
            f.write(CODEMODE_NUDGE)
        oc_json = os.path.join(cfg_dir, "opencode.json")
        with open(oc_json) as f:
            data = json.load(f)
        data.setdefault("instructions", []).append(nudge_path)
        with open(oc_json, "w") as f:
            json.dump(data, f, indent=2)
        # point the tool at the real executor + venv python, bound to the fixture workdir
        env["CODEMODE_EXEC"] = CODEMODE_EXEC
        env["CODEMODE_PYTHON"] = VENV_PY if os.path.exists(VENV_PY) else "python3"
        env["CODEMODE_ROOT"] = workdir
    return cfg_dir, env


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------
def _final_text(events_path: str) -> str:
    """Concatenate assistant text parts from the event stream (best-effort)."""
    out = []
    try:
        with open(events_path) as f:
            text = f.read()
    except OSError:
        return ""
    for ev in hm._iter_events(text):
        part = None
        if isinstance(ev, dict):
            props = ev.get("properties")
            part = (props or {}).get("part") if isinstance(props, dict) else None
            part = part or ev.get("part") or (ev if ev.get("type") == "text" else None)
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            out.append(str(part["text"]))
    return "\n".join(out)


def _transcript_blob(calls, events_path: str) -> str:
    """All assistant text + all tool outputs — what we substring-grade against."""
    parts = [_final_text(events_path)]
    for c in calls:
        if c.output:
            parts.append(str(c.output))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Run one episode
# ---------------------------------------------------------------------------
def run_episode(arm, task_id, task, model_ref, run_dir, timeout):
    workdir = tempfile.mkdtemp(prefix=f"cmab-{arm}-{task_id}-")
    shutil.rmtree(workdir)
    hm.materialize_fixture(workdir)
    xdg_root = tempfile.mkdtemp(prefix=f"cmab-xdg-{arm}-")
    try:
        cfg_dir, env = build_arm(arm, xdg_root, model_ref, workdir)
        events_path, status, wall_s = hm.run_micro_episode(
            workdir, task["prompt"], model_ref, env, run_dir, timeout, trace=False)
        calls = hm.parse_tool_calls(events_path)
        blob = _transcript_blob(calls, events_path)
        expected = task["expected"]()
        correct = expected.lower() in blob.lower()
        used_codemode = any(c.tool == "codemode" for c in calls)
        return {
            "arm": arm, "task": task_id, "status": status, "wall_s": round(wall_s, 1),
            "tool_calls": len(calls), "by_tool": _tool_hist(calls),
            "used_codemode": used_codemode, "expected": expected, "correct": correct,
            "events_path": events_path,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(xdg_root, ignore_errors=True)


def _tool_hist(calls):
    h = {}
    for c in calls:
        h[c.tool] = h.get(c.tool, 0) + 1
    return h


# ---------------------------------------------------------------------------
def cmd_run(args):
    try:
        served = he.detect_model(he.DEFAULT_BASE_URL)
    except Exception as e:
        print(f"endpoint {he.DEFAULT_BASE_URL} not reachable: {e}", file=sys.stderr)
        return 2
    model_ref = f"{he.DEFAULT_PROVIDER}/{served}"
    task_ids = args.tasks or list(TASKS)
    runs_dir = os.path.join(REPO_ROOT, "scripts", "_codemode_ab_runs")

    results = []
    for task_id in task_ids:
        task = TASKS[task_id]
        for s in range(args.k):
            for arm in args.arms:
                run_dir = os.path.join(runs_dir, f"{task_id}-{arm}-{s}")
                r = run_episode(arm, task_id, task, model_ref, run_dir, args.timeout)
                r["sample"] = s
                results.append(r)
                cm = " codemode✓" if r["used_codemode"] else ""
                print(f"  {arm:9s} {task_id:12s} #{s}: {r['status']:7s} "
                      f"calls={r['tool_calls']:<2} wall={r['wall_s']:<6} "
                      f"{'OK' if r['correct'] else 'xx'}{cm}  {r['by_tool']}")

    summary = {arm: _agg(results, arm) for arm in args.arms}
    row = {"ts": int(time.time()), "model": served, "k": args.k, "tasks": task_ids,
           "arms": args.arms, "summary": summary,
           "results": [{k: v for k, v in r.items() if k != "events_path"} for r in results]}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")
    _report(summary, args.arms)
    print(f"\nappended → {LEDGER}")
    return 0


def _agg(results, arm):
    rs = [r for r in results if r["arm"] == arm]
    if not rs:
        return {}
    n = len(rs)
    return {
        "n": n,
        "correct_rate": round(sum(r["correct"] for r in rs) / n, 3),
        "ok_rate": round(sum(r["status"] == "ok" for r in rs) / n, 3),
        "mean_calls": round(sum(r["tool_calls"] for r in rs) / n, 2),
        "mean_wall_s": round(sum(r["wall_s"] for r in rs) / n, 1),
        "codemode_used_rate": round(sum(r["used_codemode"] for r in rs) / n, 3),
    }


def _report(summary, arms):
    print(f"\n{'arm':10s} {'correct':>7s} {'ok':>5s} {'calls':>6s} {'wall_s':>7s} {'cm_used':>7s}")
    print("-" * 50)
    for arm in arms:
        a = summary.get(arm, {})
        if a:
            print(f"{arm:10s} {a['correct_rate']:>7} {a['ok_rate']:>5} {a['mean_calls']:>6} "
                  f"{a['mean_wall_s']:>7} {a['codemode_used_rate']:>7}")
    b, c = summary.get("baseline"), summary.get("codemode")
    if b and c:
        print("\nPRODUCTION ROUND-TRIP READOUT (codemode vs baseline):")
        for label, key in (("tool calls", "mean_calls"), ("wall-clock s", "mean_wall_s")):
            bv, cv = b.get(key, 0), c.get(key, 0)
            pct = round((bv - cv) / bv * 100, 1) if bv else 0
            print(f"  {label:14s}: baseline={bv}  codemode={cv}  → {pct:+}% "
                  f"({'fewer/faster' if pct > 0 else 'more/slower'})")
        print(f"  correctness   : baseline={b['correct_rate']}  codemode={c['correct_rate']}")
        print(f"  termination   : baseline ok={b['ok_rate']}  codemode ok={c['ok_rate']}")


def cmd_summary(args):
    if not os.path.exists(LEDGER):
        print("no runs yet")
        return 0
    r = [json.loads(l) for l in open(LEDGER) if l.strip()][-1]
    print(f"latest: model={r['model']} k={r['k']} tasks={r['tasks']}")
    _report(r["summary"], r["arms"])
    return 0


def cmd_selftest(args):
    """Offline: expected-value computation + arm-config materialization (no opencode)."""
    ok = True
    print("== expected values from fixture ==")
    for tid, t in TASKS.items():
        print(f"  {tid:12s} -> {t['expected']()!r}")
    # materialize a codemode arm config and assert codemode.ts + nudge landed
    xdg = tempfile.mkdtemp(prefix="cmab-selftest-")
    wd = tempfile.mkdtemp(prefix="cmab-wd-")
    try:
        cfg_dir, env = build_arm("codemode", xdg, "mlx-local/probe-model", wd)
        has_tool = os.path.exists(os.path.join(cfg_dir, "tools", "codemode.ts"))
        data = json.load(open(os.path.join(cfg_dir, "opencode.json")))
        has_nudge = any(p.endswith("codemode-nudge.md") for p in data.get("instructions", []))
        env_ok = env.get("CODEMODE_EXEC") == CODEMODE_EXEC and env.get("CODEMODE_ROOT") == wd
        print(f"\n  codemode arm: tool_installed={has_tool} nudge_registered={has_nudge} env_set={env_ok}")
        ok = has_tool and has_nudge and env_ok
        # baseline arm must NOT have codemode
        xdg2 = tempfile.mkdtemp(prefix="cmab-selftest2-")
        cfg_dir2, _ = build_arm("baseline", xdg2, "mlx-local/probe-model", wd)
        base_clean = not os.path.exists(os.path.join(cfg_dir2, "tools", "codemode.ts"))
        print(f"  baseline arm: codemode_absent={base_clean}")
        ok = ok and base_clean
        shutil.rmtree(xdg2, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        print("  ERROR:", e)
        ok = False
    finally:
        shutil.rmtree(xdg, ignore_errors=True)
        shutil.rmtree(wd, ignore_errors=True)
    print("\nSELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Production A/B: baseline vs codemode opencode (TODO 21.4b)")
    sub = p.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run")
    rn.add_argument("--arms", nargs="+", choices=["baseline", "codemode"],
                    default=["baseline", "codemode"])
    rn.add_argument("--tasks", nargs="*", default=None, choices=list(TASKS))
    rn.add_argument("--k", type=int, default=1)
    rn.add_argument("--timeout", type=float, default=600.0)
    rn.set_defaults(func=cmd_run)
    sm = sub.add_parser("summary")
    sm.set_defaults(func=cmd_summary)
    st = sub.add_parser("selftest")
    st.set_defaults(func=cmd_selftest)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
