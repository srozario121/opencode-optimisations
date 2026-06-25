#!/usr/bin/env python3
"""Niche A/B: baseline opencode vs codemode — find code-mode's real niche — TODO 21.4c.

21.4b's production A/B (`codemode_prod_ab.py`) found the 21.3 5x win is TEMPERED in
REAL opencode because the baseline reaches for `bash` and self-batches the simple
count/grep tasks into a one-liner (codemode then only used ~24% of the time). The
open question 21.4c answers: **is there a regime where code-mode separates CLEANLY
from a bash-equipped baseline?** The hypothesis (item-21 survey): code-mode should
win when bash is a *poor fit* — multi-step parsing, conditional logic on file
contents, cross-file reasoning — where a single shell one-liner is awkward/fragile
and the model is forced into a churn of read/grep round-trips instead.

So this harness keeps the SAME two arms as 21.4b (the baseline still has `bash` —
that is the honest, un-nerfed comparison) and only changes the TASKS: every task here
is deliberately bash-hostile. If code-mode's niche is real, codemode should show a
clean round-trip / wall-clock / termination separation on THESE tasks that it could
not show on the simple 21.4b tasks.

It reuses 21.4b's verified machinery wholesale (`codemode_prod_ab.build_arm` for arm
config materialization + the codemode nudge, the isolated-config + `opencode run
--format json` episode driver from `harness_micro`, the transcript parser). The only
local additions are (1) the bash-poor task set with fixture-computed expected answers
and (2) word-boundary correctness grading (small integer answers spuriously
substring-match; `\bN\b` is stricter). Per the established 21.x methodology the
PRIMARY signal is round-trips + wall-clock + termination + codemode-used-rate;
correctness is best-effort/secondary.

Usage:
  scripts/codemode_niche_ab.py run --k 5                       # both arms, all tasks
  scripts/codemode_niche_ab.py run --tasks orphan_count --arms baseline codemode --k 5
  scripts/codemode_niche_ab.py summary
  scripts/codemode_niche_ab.py selftest                        # offline (no opencode/model)

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
import codemode_prod_ab as cp  # noqa: E402  (reuse 21.4b arm + transcript machinery)
import harness_eval as he  # noqa: E402
import harness_micro as hm  # noqa: E402  (reuse config/episode/transcript machinery)

REPO_ROOT = hm.REPO_ROOT
LEDGER = os.path.join(REPO_ROOT, "scripts", "codemode-niche-ab.jsonl")

# 21.4b's nudge never told the model the sandbox is RESTRICTED Python — so on these
# parse-heavy tasks the local model reaches for `import re` and the call dies with
# "ImportError: __import__ not found" (verified in the 21.4c smoke test). The sandbox
# is builtins-only by design (no import/open/eval); str methods + the host-tools are
# enough (a builtins-only const_sum program returns the right answer in 2 host-calls).
# This nudge makes that constraint explicit so the niche test measures code-mode's real
# ceiling, not a fixable doc gap. It is set on `cp.CODEMODE_NUDGE` (the module constant
# `build_arm` reads) only for this run — the 21.4b script/file is untouched.
NICHE_NUDGE = (
    "# Tool guidance\n\n"
    "When a task needs you to read, scan, parse, count, or aggregate across one or more "
    "files — especially with multi-step logic or a condition on file contents — use the "
    "`codemode` tool: write ONE small Python program that calls the host-tools "
    "(read_file, read_lines, list_files, glob, grep) and assigns the answer to `result`. "
    "Prefer ONE codemode call over many separate read/grep calls — it is much faster.\n\n"
    "IMPORTANT — the codemode sandbox runs RESTRICTED Python:\n"
    "* NO `import` statements (they fail), and no `open`/`eval`.\n"
    "* Use ONLY Python builtins (str methods like .split/.strip/.startswith/.isdigit, "
    "plus sum/int/sorted/len/etc.) and the host-tools above.\n"
    "* For text parsing do it with string methods — do NOT `import re`.\n"
    "Example: `total=0\\nfor ln in read_file('config.py').splitlines():\\n    ...`  "
    "then `result = total`.\n"
)


# ---------------------------------------------------------------------------
# Tasks — deliberately bash-HOSTILE: multi-step parse, conditional logic on file
# contents, cross-file reasoning. Expected answers are computed from the fixture so
# they stay correct if the fixture changes. (Same `_fx` fixture as 21.4b.)
# ---------------------------------------------------------------------------
def _fx(path):
    return os.path.join(hm.fixture_root(), path)


def _store_defs():
    """Top-level `def ` names in store.py (column-0 defs only)."""
    return [ln[4:].split("(", 1)[0].strip()
            for ln in open(_fx("store.py")) if ln.startswith("def ")]


def _exp_orphan_count():
    """store.py functions whose name never appears anywhere in main.py (cross-file)."""
    main = open(_fx("main.py")).read()
    return str(sum(1 for d in _store_defs() if d not in main))


def _exp_add_docstring_count():
    """store.py functions whose docstring's first word is 'Add' (parse def->docstring)."""
    lines = open(_fx("store.py")).read().splitlines()
    n = 0
    for i, ln in enumerate(lines):
        if ln.startswith("def "):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            doc = lines[j].strip() if j < len(lines) else ""
            m = re.match(r'"""\s*(\w+)', doc)
            if m and m.group(1) == "Add":
                n += 1
    return str(n)


def _exp_sentinel_digit_sum():
    """Extract SENTINEL_TOKEN_<n> from data/notes.txt, sum its decimal digits."""
    m = re.search(r"SENTINEL_TOKEN_(\d+)", open(_fx("data/notes.txt")).read())
    return str(sum(int(c) for c in m.group(1)))


def _exp_const_sum():
    """Sum every module-level integer constant (UPPER = <int>) in store.py + config.py."""
    total = 0
    for f in ("store.py", "config.py"):
        for ln in open(_fx(f)):
            m = re.match(r"[A-Z][A-Z0-9_]+\s*=\s*(-?\d+)\s*$", ln)
            if m:
                total += int(m.group(1))
    return str(total)


TASKS = {
    # cross-file set difference: names defined here but referenced there
    "orphan_count": {
        "prompt": ("Consider every top-level function defined in store.py. How many of "
                   "their names NEVER appear anywhere in main.py? Report just the integer."),
        "expected": _exp_orphan_count,
    },
    # parse each def's docstring, test a condition on its first word
    "add_docstring_count": {
        "prompt": ("In store.py, look at the one-line docstring of each top-level function. "
                   "How many of those docstrings have 'Add' as their first word? "
                   "Report just the integer."),
        "expected": _exp_add_docstring_count,
    },
    # extract a token from prose, then do arithmetic on its characters
    "sentinel_digit_sum": {
        "prompt": ("The file data/notes.txt mentions a sentinel token of the form "
                   "SENTINEL_TOKEN_<number>. Take that number and report the SUM of its "
                   "decimal digits. Report just the integer."),
        "expected": _exp_sentinel_digit_sum,
    },
    # conditional aggregation: only integer-valued UPPER_CASE constants, summed across files
    "const_sum": {
        "prompt": ("Across store.py and config.py, find every module-level constant whose "
                   "name is ALL_CAPS and whose value is a plain integer (ignore floats, "
                   "strings and booleans). Report the SUM of those integer values."),
        "expected": _exp_const_sum,
    },
}


# ---------------------------------------------------------------------------
# Run one episode — reuses 21.4b arm + episode machinery; word-boundary grading.
# ---------------------------------------------------------------------------
def _graded(blob: str, expected: str) -> bool:
    """Stricter than 21.4b's raw substring: small integer answers spuriously
    substring-match, so require a numeric/word boundary around the expected token.
    Rejects the token embedded in a larger number ("83" in "8312"), inside an
    identifier ("5" in "v5"), or as a float's part ("5" in "5.2"/"1.5"), while still
    accepting trailing sentence punctuation ("5.")."""
    pat = rf"(?<![\w.]){re.escape(expected)}(?![\w])(?!\.\d)"
    return re.search(pat, blob) is not None


def run_episode(arm, task_id, task, model_ref, run_dir, timeout):
    workdir = tempfile.mkdtemp(prefix=f"cmn-{arm}-{task_id}-")
    shutil.rmtree(workdir)
    hm.materialize_fixture(workdir)
    xdg_root = tempfile.mkdtemp(prefix=f"cmn-xdg-{arm}-")
    try:
        cfg_dir, env = cp.build_arm(arm, xdg_root, model_ref, workdir)
        events_path, status, wall_s = hm.run_micro_episode(
            workdir, task["prompt"], model_ref, env, run_dir, timeout, trace=False)
        calls = hm.parse_tool_calls(events_path)
        blob = cp._transcript_blob(calls, events_path)
        expected = task["expected"]()
        correct = _graded(blob, expected)
        used_codemode = any(c.tool == "codemode" for c in calls)
        used_bash = any(c.tool == "bash" for c in calls)
        return {
            "arm": arm, "task": task_id, "status": status, "wall_s": round(wall_s, 1),
            "tool_calls": len(calls), "by_tool": cp._tool_hist(calls),
            "used_codemode": used_codemode, "used_bash": used_bash,
            "expected": expected, "correct": correct, "events_path": events_path,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(xdg_root, ignore_errors=True)


# ---------------------------------------------------------------------------
def cmd_run(args):
    cp.CODEMODE_NUDGE = NICHE_NUDGE  # builtins-only guidance for the sandbox (see top)
    try:
        served = he.detect_model(he.DEFAULT_BASE_URL)
    except Exception as e:  # noqa: BLE001
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
                run_dir = os.path.join(runs_dir, f"niche-{task_id}-{arm}-{s}")
                r = run_episode(arm, task_id, task, model_ref, run_dir, args.timeout)
                r["sample"] = s
                results.append(r)
                cm = " cm✓" if r["used_codemode"] else ""
                bh = " bash✓" if r["used_bash"] else ""
                print(f"  {arm:9s} {task_id:20s} #{s}: {r['status']:7s} "
                      f"calls={r['tool_calls']:<2} wall={r['wall_s']:<6} "
                      f"{'OK' if r['correct'] else 'xx'}{cm}{bh}  {r['by_tool']}")

    summary = {arm: _agg(results, arm) for arm in args.arms}
    by_task = {t: {arm: _agg([r for r in results if r["task"] == t], arm)
                   for arm in args.arms} for t in task_ids}
    row = {"ts": int(time.time()), "model": served, "k": args.k, "tasks": task_ids,
           "arms": args.arms, "timeout": args.timeout, "summary": summary,
           "by_task": by_task,
           "results": [{k: v for k, v in r.items() if k != "events_path"} for r in results]}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")
    _report(summary, by_task, args.arms)
    print(f"\nappended -> {LEDGER}")
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
        "timeout_rate": round(sum(r["status"] == "timeout" for r in rs) / n, 3),
        "mean_calls": round(sum(r["tool_calls"] for r in rs) / n, 2),
        "mean_wall_s": round(sum(r["wall_s"] for r in rs) / n, 1),
        "codemode_used_rate": round(sum(r["used_codemode"] for r in rs) / n, 3),
        "bash_used_rate": round(sum(r["used_bash"] for r in rs) / n, 3),
    }


def _delta_block(b, c):
    out = []
    for label, key in (("tool calls", "mean_calls"), ("wall-clock s", "mean_wall_s")):
        bv, cv = b.get(key, 0), c.get(key, 0)
        pct = round((bv - cv) / bv * 100, 1) if bv else 0
        out.append(f"  {label:14s}: baseline={bv}  codemode={cv}  -> {pct:+}% "
                   f"({'fewer/faster' if pct > 0 else 'more/slower'})")
    out.append(f"  correctness   : baseline={b.get('correct_rate')}  "
               f"codemode={c.get('correct_rate')}")
    out.append(f"  termination   : "
               f"baseline ok={b.get('ok_rate')} (timeout={b.get('timeout_rate')})  "
               f"codemode ok={c.get('ok_rate')} (timeout={c.get('timeout_rate')})")
    out.append(f"  tool usage    : baseline bash={b.get('bash_used_rate')}  "
               f"codemode cm={c.get('codemode_used_rate')} bash={c.get('bash_used_rate')}")
    return "\n".join(out)


def _report(summary, by_task, arms):
    print(f"\n{'arm':10s} {'correct':>7s} {'ok':>5s} {'calls':>6s} {'wall_s':>7s} "
          f"{'cm_use':>6s} {'bash':>5s}")
    print("-" * 56)
    for arm in arms:
        a = summary.get(arm, {})
        if a:
            print(f"{arm:10s} {a['correct_rate']:>7} {a['ok_rate']:>5} {a['mean_calls']:>6} "
                  f"{a['mean_wall_s']:>7} {a['codemode_used_rate']:>6} {a['bash_used_rate']:>5}")
    b, c = summary.get("baseline"), summary.get("codemode")
    if b and c:
        print("\nNICHE ROUND-TRIP READOUT (codemode vs bash-equipped baseline), ALL tasks:")
        print(_delta_block(b, c))
        print("\nPER-TASK (where does codemode separate?):")
        for t, arms_agg in by_task.items():
            tb, tc = arms_agg.get("baseline"), arms_agg.get("codemode")
            if tb and tc:
                print(f"\n[{t}]")
                print(_delta_block(tb, tc))


def cmd_summary(args):
    if not os.path.exists(LEDGER):
        print("no runs yet")
        return 0
    r = [json.loads(line) for line in open(LEDGER) if line.strip()][-1]
    print(f"latest: model={r['model']} k={r['k']} tasks={r['tasks']} timeout={r.get('timeout')}")
    _report(r["summary"], r.get("by_task", {}), r["arms"])
    return 0


def cmd_selftest(args):
    """Offline: expected-value computation + arm-config materialization + grader."""
    ok = True
    print("== expected values from fixture (bash-poor tasks) ==")
    expected_now = {}
    for tid, t in TASKS.items():
        v = t["expected"]()
        expected_now[tid] = v
        print(f"  {tid:20s} -> {v!r}")
    # sanity: the values we currently expect from the committed fixture
    want = {"orphan_count": "5", "add_docstring_count": "2",
            "sentinel_digit_sum": "12", "const_sum": "83"}
    for tid, w in want.items():
        if expected_now.get(tid) != w:
            print(f"  MISMATCH {tid}: got {expected_now.get(tid)!r} want {w!r}")
            ok = False

    print("\n== word-boundary grader ==")
    grade_cases = [
        ("the total is 83 across both files", "83", True),
        ("address 8312 has no constant", "83", False),   # 83 inside 8312 must NOT match
        ("answer: 5.", "5", True),
        ("v5 is unrelated", "5", False),                 # 5 inside v5 must NOT match
    ]
    for blob, exp, want_ok in grade_cases:
        got = _graded(blob, exp)
        flag = "ok" if got == want_ok else "FAIL"
        if got != want_ok:
            ok = False
        print(f"  grade({blob!r}, {exp!r}) = {got} (want {want_ok}) [{flag}]")

    print("\n== arm-config materialization (reuses 21.4b build_arm) ==")
    xdg = tempfile.mkdtemp(prefix="cmn-selftest-")
    wd = tempfile.mkdtemp(prefix="cmn-wd-")
    try:
        cfg_dir, env = cp.build_arm("codemode", xdg, "mlx-local/probe-model", wd)
        has_tool = os.path.exists(os.path.join(cfg_dir, "tools", "codemode.ts"))
        data = json.load(open(os.path.join(cfg_dir, "opencode.json")))
        has_nudge = any(p.endswith("codemode-nudge.md") for p in data.get("instructions", []))
        env_ok = env.get("CODEMODE_EXEC") == cp.CODEMODE_EXEC and env.get("CODEMODE_ROOT") == wd
        print(f"  codemode arm: tool_installed={has_tool} "
              f"nudge_registered={has_nudge} env_set={env_ok}")
        ok = ok and has_tool and has_nudge and env_ok
        xdg2 = tempfile.mkdtemp(prefix="cmn-selftest2-")
        cfg_dir2, _ = cp.build_arm("baseline", xdg2, "mlx-local/probe-model", wd)
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
    p = argparse.ArgumentParser(description="Niche A/B: baseline vs codemode opencode (TODO 21.4c)")
    sub = p.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run")
    rn.add_argument("--arms", nargs="+", choices=["baseline", "codemode"],
                    default=["baseline", "codemode"])
    rn.add_argument("--tasks", nargs="*", default=None, choices=list(TASKS))
    rn.add_argument("--k", type=int, default=5)
    rn.add_argument("--timeout", type=float, default=600.0)  # Gemma-tuned cap (item 22)
    rn.set_defaults(func=cmd_run)
    sm = sub.add_parser("summary")
    sm.set_defaults(func=cmd_summary)
    st = sub.add_parser("selftest")
    st.set_defaults(func=cmd_selftest)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
