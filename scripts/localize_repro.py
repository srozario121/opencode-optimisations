#!/usr/bin/env python3
"""item 31.3 (tailored tool) — reproduce a bug and localize it from the traceback.

Backs the `localize` opencode tool. Runs a user-provided Python snippet (the
failing call from the issue) IN THE INSTANCE VENV (so `import <repo>` works), and
returns a compact, deterministic localization: the exception, the recursion cycle
(consecutive repeated frames collapsed with a repeat count), and a source window
around each relevant in-repo frame. This collapses the weak model's
reproduce -> read-traceback -> read-each-file exploration loop (5-8 slow tool
rounds on the 16 GB M1) into ONE deterministic call, and removes the variance of
the model having to author the repro + parse a 1000-frame RecursionError by hand.

Usage:  python localize_repro.py <snippet_file> [--ctx N] [--max-frames N]
Output: a single line `LOCALIZE_JSON:{...}` (so the .ts wrapper can parse the last
line regardless of any stdout the snippet itself printed).

Only frames whose file is UNDER the cwd (the repo checkout) are reported — stdlib
and the wrapper itself are filtered out, so the model sees only editable source.
"""
from __future__ import annotations

import json
import os
import sys
import traceback


def _window(root: str, relpath: str, line: int, ctx: int) -> list[dict]:
    """±ctx source lines (1-indexed) around `line` of repo file `relpath`."""
    try:
        with open(os.path.join(root, relpath)) as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    a = max(0, line - ctx - 1)
    b = min(len(lines), line + ctx)
    return [{"n": a + 1 + k, "t": lines[a + k]} for k in range(b - a)]


def _auto_import_preamble(root: str) -> str:
    """Best-effort `from <pkg> import *` for each top-level package in the repo.

    The weak model reliably extracts the failing expression but often FORGETS the
    import (e.g. writes `sympify(...)` with no `from sympy import sympify`), so the
    snippet dies with a NameError before reaching library code and the tool gives
    nothing useful. Pre-importing the repo's own top-level package(s) makes such
    import-less snippets reproduce faithfully (same as `import <pkg>`). Wrapped so a
    failing import never aborts the repro; <repro> line shifts don't matter because
    only in-repo frames are reported."""
    pkgs: list[str] = []
    try:
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if (name.isidentifier() and os.path.isdir(d)
                    and os.path.exists(os.path.join(d, "__init__.py"))):
                pkgs.append(name)
    except OSError:
        return ""
    return "\n".join(
        f"try:\n    from {p} import *\nexcept Exception:\n    pass" for p in pkgs[:4])


def _localize(snippet_path: str, ctx: int, max_frames: int) -> dict:
    root = os.getcwd()
    with open(snippet_path) as f:
        src = f.read()
    preamble = _auto_import_preamble(root)
    src = preamble + "\n" + src if preamble else src
    glob_ns: dict = {"__name__": "__main__"}
    try:
        code = compile(src, "<repro>", "exec")
    except SyntaxError as e:
        return {"ok": False, "kind": "snippet-syntax-error",
                "exc": f"{type(e).__name__}: {e}", "frames": []}
    try:
        exec(code, glob_ns)  # noqa: S102 — running the user's repro is the point
    except BaseException as e:  # noqa: BLE001 — we want ANY failure, incl. RecursionError
        tb = sys.exc_info()[2]
        raw = traceback.extract_tb(tb)
        # Keep only in-repo frames (editable source); drop stdlib + the wrapper.
        # Track first-seen depth so we can break frequency ties toward the
        # SHALLOWEST frame (the one that ENTERS the recursive cycle = the fix site,
        # not the deep generic-traversal plumbing the cycle bottoms out in).
        repo: list[tuple[str, int, str]] = []
        first_depth: dict[tuple, int] = {}
        for d, fr in enumerate(raw):
            fn = fr.filename
            if fn in ("<repro>", snippet_path) or not fn.startswith(root + os.sep):
                continue
            key = (os.path.relpath(fn, root), fr.lineno or 0, fr.name)
            repo.append(key)
            first_depth.setdefault(key, d)
        if not repo:
            # The error happened before reaching library code (e.g. a NameError from
            # a missing import the auto-import didn't cover) — nothing to localize.
            return {
                "ok": False,
                "kind": "no-repo-frames",
                "exc": f"{type(e).__name__}: {str(e)[:200]}",
                "n_repo_frames": 0,
                "frames": [],
                "hint": "The code failed before reaching the library. Make sure the "
                        "snippet imports and calls the failing API exactly as in the "
                        "issue, then re-call localize.",
            }
        # The recursion CYCLE = the distinct repo frames that repeat (A→B→C→A→…),
        # surfaced by frequency across the WHOLE stack (consecutive-collapse misses
        # a multi-frame cycle). Order by repeat-count desc, then shallowest-first so
        # the cycle's entry frame (the domain logic to fix) ranks above the deep
        # generic plumbing it recurses through.
        counts: dict[tuple, int] = {}
        for key in repo:
            counts[key] = counts.get(key, 0) + 1
        ranked = sorted(counts.items(),
                        key=lambda kv: (-kv[1], first_depth[kv[0]]))
        frames = [{
            "file": fr[0], "line": fr[1], "func": fr[2], "repeat": cnt,
            "src": _window(root, fr[0], fr[1], ctx),
        } for fr, cnt in ranked[:max_frames]]
        # Driver = the shallowest CYCLIC frame that is domain logic, not generic
        # dispatch/caching plumbing (a memoization wrapper or a base `__new__`/dunder
        # is in the cycle but is never the thing to edit). This is a best-effort hint;
        # the full cycle is returned so the model can judge.
        # Skip generic dispatch/caching/operator frames — they are IN the cycle but
        # are never the thing to edit (the fix lives in the function that implements
        # the failing operation, e.g. `Abs.eval`, not the `__abs__` operator hook or
        # the memoization wrapper).
        _GENERIC = {"wrapper", "__new__", "__call__", "__instancecheck__",
                    "__getattr__", "__getattribute__", "decorated", "func",
                    "binary_op", "_func", "flatten",
                    "__abs__", "__mul__", "__add__", "__sub__", "__pow__",
                    "__neg__", "__truediv__", "__radd__", "__rmul__", "__eq__"}
        cyclic = [f for f in frames if f["repeat"] > 1]
        cyclic.sort(key=lambda f: first_depth[(f["file"], f["line"], f["func"])])
        domain = [f for f in cyclic if f["func"] not in _GENERIC]
        driver = (domain or cyclic)[0] if cyclic else None
        return {
            "ok": False,
            "kind": "exception",
            "exc": f"{type(e).__name__}: {str(e)[:200]}",
            "n_repo_frames": len(repo),
            "n_distinct_frames": len(counts),
            "driver": (f"{driver['file']}:{driver['line']} in {driver['func']}"
                       if driver else None),
            "frames": frames,
        }
    return {"ok": True, "kind": "no-error",
            "note": "the snippet ran with no exception"}


def main(argv: list[str]) -> int:
    if not argv:
        print("LOCALIZE_JSON:" + json.dumps(
            {"ok": False, "kind": "usage", "exc": "no snippet file given"}))
        return 2
    snippet = argv[0]
    ctx, max_frames = 5, 8
    for i, a in enumerate(argv):
        if a == "--ctx" and i + 1 < len(argv):
            ctx = int(argv[i + 1])
        if a == "--max-frames" and i + 1 < len(argv):
            max_frames = int(argv[i + 1])
    out = _localize(snippet, ctx, max_frames)
    print("LOCALIZE_JSON:" + json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
