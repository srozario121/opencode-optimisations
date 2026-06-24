#!/usr/bin/env python3
"""Real code-mode executor — TODO 21.4 (productionise the 21.3 win).

The 21.3 A/B (`codemode_ab.py`) proved that collapsing N tool calls into ONE
sandboxed code block is ~5x faster and far more reliable than the flat ReAct loop
— but that was a proxy harness with MOCK tools. This is the production executor:
it binds the SAME sandbox (validated in `codegen_probe.run_exec`/`run_monty`) to
REAL filesystem host-tools, so a model's single Python code block can do many real
file operations in one shot and return only the consolidated `result`.

It is driven by the opencode custom tool `.opencode/tools/codemode.ts`, which passes
the model's code on stdin and reads back a JSON envelope on stdout.

Host tools exposed to the sandboxed code (read-only by default):
  read_file(path)            -> str            file contents (UTF-8)
  list_files(dir=".")        -> list[str]      file paths directly under a dir
  glob(pattern)              -> list[str]       glob matches (supports **)
  grep(pattern, path=".")    -> list[str]       "relpath:lineno:line" matches (Python re)
  read_lines(path, start=1, end=None) -> list[str]   lines start..end, 1-indexed inclusive
  --allow-bash adds:  bash(cmd) -> str          stdout of a shell command
  --allow-write adds: write_file(path, text) -> int   bytes written

Security model (matches the threat model: single-user LOCAL dev tool, offline):
the sandboxed code can ONLY call the injected host-tools + a safe builtin set
(no import/open/eval) — the same restricted namespace the probe validated. Paths
are resolved under --root and may not escape it. bash/write are OFF unless opted in.
This is a usability sandbox, not a hard security boundary.

CLI:
  echo '<code>' | scripts/codemode_exec.py [--root DIR] [--engine exec|monty]
                                            [--allow-bash] [--allow-write] [--timeout S]
  scripts/codemode_exec.py --code-file f.py ...
  scripts/codemode_exec.py --selftest          # offline, writes a temp tree, no model

Output (stdout): one JSON object —
  {"ok": true,  "result": <json-or-repr>, "result_repr": str, "calls": [..], "n_calls": int}
  {"ok": false, "error": "<msg>", "calls": [..]}
Exit codes: 0 always for a graded run (errors are in the envelope); 2 usage.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegen_probe as cp  # noqa: E402  (reuse the VALIDATED sandbox engines)


def _resolve(root: str, path: str) -> str:
    """Resolve `path` under `root`; refuse to escape the root."""
    full = os.path.realpath(os.path.join(root, path))
    root_real = os.path.realpath(root)
    if full != root_real and not full.startswith(root_real + os.sep):
        raise PermissionError(f"path escapes root: {path}")
    return full


def make_real_tools(root: str, call_log: list, allow_bash: bool, allow_write: bool) -> dict:
    """Build REAL filesystem host-tools bound to `root`; log each call."""

    def logged(name, fn):
        def wrapper(*a, **k):
            call_log.append({"tool": name, "args": [_short(x) for x in a]})
            return fn(*a, **k)
        return wrapper

    def read_file(path):
        with open(_resolve(root, path), encoding="utf-8", errors="replace") as f:
            return f.read()

    def read_lines(path, start=1, end=None):
        # 1-indexed, INCLUSIVE of both ends (matches how line numbers read and the
        # custom read tool's `N | line` gutter). end=None -> through the last line.
        with open(_resolve(root, path), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[max(0, start - 1):end]

    def list_files(d="."):
        base = _resolve(root, d)
        return sorted(
            os.path.relpath(os.path.join(base, e), root)
            for e in os.listdir(base)
            if os.path.isfile(os.path.join(base, e))
        )

    def glob(pattern):
        hits = _glob.glob(os.path.join(root, pattern), recursive=True)
        return sorted(os.path.relpath(h, root) for h in hits if os.path.isfile(h))

    def grep(pattern, path="."):
        rx = re.compile(pattern)
        target = _resolve(root, path)
        files = ([target] if os.path.isfile(target)
                 else [os.path.join(dp, fn) for dp, _, fns in os.walk(target) for fn in fns])
        out = []
        for fp in files:
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            out.append(f"{os.path.relpath(fp, root)}:{i}:{line.rstrip()}")
                            if len(out) >= 500:
                                return out
            except (OSError, UnicodeError):
                continue
        return out

    tools = {
        "read_file": logged("read_file", read_file),
        "read_lines": logged("read_lines", read_lines),
        "list_files": logged("list_files", list_files),
        "glob": logged("glob", glob),
        "grep": logged("grep", grep),
    }

    if allow_bash:
        def bash(cmd):
            r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True,
                               text=True, timeout=60)
            return (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr else "")
        tools["bash"] = logged("bash", bash)

    if allow_write:
        def write_file(path, text):
            full = _resolve(root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                return f.write(text)
        tools["write_file"] = logged("write_file", write_file)

    return tools


def _short(x):
    s = repr(x)
    return s if len(s) <= 80 else s[:77] + "..."


def _jsonable(v):
    try:
        json.dumps(v)
        return v, json.dumps(v)
    except (TypeError, ValueError):
        return None, repr(v)


def execute(code: str, root: str, engine: str, allow_bash: bool,
            allow_write: bool, timeout: float) -> dict:
    root = os.path.realpath(root)  # normalize so host-tool relpaths are clean
    call_log: list = []
    tools = make_real_tools(root, call_log, allow_bash, allow_write)
    runner = cp.ENGINES[engine]
    result, err = runner(code, tools, timeout_s=timeout)
    if err is not None:
        return {"ok": False, "error": err, "calls": call_log, "n_calls": len(call_log)}
    jv, jrepr = _jsonable(result)
    return {"ok": True, "result": jv, "result_repr": jrepr,
            "calls": call_log, "n_calls": len(call_log)}


# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "src"))
        files = {"src/a.py": "import os\n# TODO fix\nx=1\n", "src/b.py": "y=2\n",
                 "src/c.py": "def f():\n    return 3\n\nf()\n"}
        for p, t in files.items():
            with open(os.path.join(d, p), "w") as f:
                f.write(t)

        # one code block, many real ops -> one consolidated result
        code = ("total = 0\n"
                "for p in glob('src/*.py'):\n"
                "    total += len(read_file(p).splitlines())\n"
                "result = total\n")
        out = execute(code, d, "exec", False, False, 5.0)
        expect = sum(len(t.splitlines()) for t in files.values())
        print("sum_lines:", out["ok"], "result=", out.get("result"), "expected=", expect,
              "n_calls=", out["n_calls"])
        ok = ok and out["ok"] and out["result"] == expect

        # grep host-tool
        code2 = "result = [m for m in grep('TODO', 'src')]"
        out2 = execute(code2, d, "exec", False, False, 5.0)
        print("grep TODO:", out2["ok"], out2.get("result"))
        ok = ok and out2["ok"] and any("a.py" in m for m in (out2["result"] or []))

        # path escape is refused
        out3 = execute("result = read_file('../../../etc/hosts')", d, "exec", False, False, 5.0)
        print("escape refused:", (not out3["ok"]), out3.get("error", "")[:50])
        ok = ok and (not out3["ok"])

        # bash gated off by default
        out4 = execute("result = bash('echo hi')", d, "exec", False, False, 5.0)
        print("bash gated:", (not out4["ok"]), out4.get("error", "")[:40])
        ok = ok and (not out4["ok"])

        # bash works when allowed
        out5 = execute("result = bash('echo hi').strip()", d, "exec", True, False, 5.0)
        print("bash allowed:", out5["ok"], out5.get("result"))
        ok = ok and out5["ok"] and out5["result"] == "hi"

    print("\nSELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Real code-mode executor (TODO 21.4)")
    p.add_argument("--root", default=os.getcwd(), help="root dir host-tools are bound to")
    p.add_argument("--engine", choices=list(cp.ENGINES), default="exec")
    p.add_argument("--code-file", default=None, help="read code from file (default: stdin)")
    p.add_argument("--allow-bash", action="store_true")
    p.add_argument("--allow-write", action="store_true")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()

    code = open(args.code_file).read() if args.code_file else sys.stdin.read()
    # tolerate a fenced block being passed through verbatim
    extracted, _ = cp.extract_code(code)
    code = extracted or code
    out = execute(code, os.path.realpath(args.root), args.engine,
                  args.allow_bash, args.allow_write, args.timeout)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
