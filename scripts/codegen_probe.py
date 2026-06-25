#!/usr/bin/env python3
"""Code-generation orchestration probe — TODO 21.2 decisive experiment.

The sandbox-codeexec survey (TODO 21.1, ``docs/sandbox-codeexec-research.md``)
concluded that the "code mode" pattern (emit ONE code block that calls N host
tools with loops/conditionals, run it in a sandbox, return only the consolidated
result) is mechanistically sound and that Monty is deployable offline — BUT that
the make-or-break question is unmeasured: **can a ~4B local model actually emit
correct orchestration code?** Every quantified win in the literature came from
frontier models, and small models hit a documented "structure tax".

This instrument measures exactly that one number — **pass@1 at emitting a single
correct orchestration code block** — and is deliberately MODEL-AGNOSTIC so the
SAME frozen tasks run against (a) the local Gemma-4-E4B and (b) any online model
(e.g. opencode's hosted models) to isolate **whether model size is the issue**.
If the online model passes where Gemma fails, size is confirmed as the blocker;
if both fail, the pattern is wrong for this task shape regardless of stack.

What it does NOT do: run the full opencode agentic loop, touch the real config,
or require SWE-bench. It is a clean one-shot completion probe — the cheapest
possible signal before committing to any sandbox integration.

Design choices grounded in the survey:
  * **Markdown code block, NOT JSON-wrapped.** The "structure tax" finding
    specifically condemns JSON-enveloped code; plain markdown is less penalized
    for small models. The prompt asks for a single ```python fence.
  * **Data-free task descriptions.** The mock fixture values live ONLY in the
    executor, never in the prompt — so the model literally cannot hardcode the
    numeric answer; a correct ``result`` proves it actually orchestrated.
  * **Execution against mocks is the pass signal.** A generated program passes
    only if it (1) emits a clean code block, (2) parses, (3) calls the required
    host tools at runtime, (4) uses real control flow / multi-call chaining, and
    (5) produces the correct ``result``. Sub-stage rates are reported so you see
    WHERE a model fails (format vs syntax vs logic), per the survey's diagnostics.
  * **Executor is pluggable.** Default ``exec`` engine = a restricted in-process
    sandbox (limited builtins, host tools injected, SIGALRM timeout) — the survey
    says this is the right primary for measuring code-gen. An optional ``monty``
    engine hook is stubbed for later faithfulness once pydantic-monty stabilises.

Transports (how we reach a model):
  * ``http``     — POST {base_url}/chat/completions (OpenAI-compatible). Default
                   for the local MLX repair-proxy on :8080 and for any online
                   OpenAI-compatible endpoint. Full prompt control = cleanest pass@1.
  * ``opencode`` — drive ``opencode run --format json -m <model>`` with tools off,
                   for online models reachable ONLY through opencode's providers.
                   (Caveat: opencode injects its own system prompt; less controlled.)

Usage:
  scripts/codegen_probe.py selftest                       # offline, no model — proves the grader
  scripts/codegen_probe.py targets                        # list configured targets
  scripts/codegen_probe.py run --target local-gemma --k 5
  scripts/codegen_probe.py run --target bigpickle --k 5   # online model (configure target first)
  scripts/codegen_probe.py run --target local-gemma --tasks sum_lines find_todo
  scripts/codegen_probe.py summary                        # side-by-side pass@1 per target

Exit codes: 0 ok · 2 usage/config (bad target, endpoint down) · 1 run error.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
TARGETS_FILE = os.path.join(SCRIPTS_DIR, "codegen_probe_targets.json")
LEDGER = os.path.join(SCRIPTS_DIR, "codegen-probe.jsonl")

# ---------------------------------------------------------------------------
# Frozen mock fixture — lives ONLY here, never in a prompt. The model cannot
# hardcode answers it never sees. Reference solutions below compute expected
# results directly from this dict, independent of the model's tool calls.
# ---------------------------------------------------------------------------
FIXTURE = {
    "files": {
        "src/a.py": "import os\n# TODO: refactor this\nx = 1\n",      # 3 lines, has TODO
        "src/b.py": "y = 2\nprint(y)\n",                              # 2 lines
        "src/c.py": "def f():\n    return 3\n\nf()\n",                # 4 lines
        "config.json": '{"debug": true, "plugins": ["otel", "repair", "grep"]}',
    },
    "dirs": {"src": ["src/a.py", "src/b.py", "src/c.py"]},
    "users": {
        1: {"id": 1, "balance": 50},
        2: {"id": 2, "balance": 120},
        3: {"id": 3, "balance": 80},
    },
    "orders": {
        1: [{"amount": 20}],
        2: [{"amount": 5}, {"amount": 30}],
        3: [],
        42: [{"amount": 5}, {"amount": 15}, {"amount": 25}],
        7: [{"amount": 3}],
    },
    # list_user_ids() yields 7 and 99 which have NO user record -> get_user raises;
    # the hard "error handling" task must skip them.
    "user_ids": [1, 2, 3, 7, 99],
    "products": {
        100: {"id": 100, "price": 10, "stock": 3},
        101: {"id": 101, "price": 50, "stock": 0},
    },
}


def _make_tools(call_log):
    """Build mock host tools bound to FIXTURE; every call is recorded in call_log."""

    def logged(name, fn):
        def wrapper(*a, **k):
            call_log.append(name)
            return fn(*a, **k)

        return wrapper

    def list_files(d):
        return list(FIXTURE["dirs"].get(d, []))

    def read_file(p):
        if p not in FIXTURE["files"]:
            raise FileNotFoundError(p)
        return FIXTURE["files"][p]

    def count_lines(text):
        return len(text.splitlines())

    def parse_json(text):
        return json.loads(text)

    def get_user(uid):
        if uid not in FIXTURE["users"]:
            raise KeyError(uid)
        return dict(FIXTURE["users"][uid])

    def get_orders(uid):
        return [dict(o) for o in FIXTURE["orders"].get(uid, [])]

    # --- extra tools: some used by hard tasks, some pure DISTRACTORS so the
    # model must select the right ones from a larger menu (10-13 tools). ---
    def list_user_ids():
        return list(FIXTURE["user_ids"])

    def file_size(p):
        if p not in FIXTURE["files"]:
            raise FileNotFoundError(p)
        return len(FIXTURE["files"][p])

    def grep_count(pattern, text):
        return text.count(pattern)

    def get_product(pid):
        if pid not in FIXTURE["products"]:
            raise KeyError(pid)
        return dict(FIXTURE["products"][pid])

    def to_upper(s):
        return s.upper()

    def sum_list(nums):
        return sum(nums)

    def now():
        return 1700000000

    return {
        "list_files": logged("list_files", list_files),
        "read_file": logged("read_file", read_file),
        "count_lines": logged("count_lines", count_lines),
        "parse_json": logged("parse_json", parse_json),
        "get_user": logged("get_user", get_user),
        "get_orders": logged("get_orders", get_orders),
        "list_user_ids": logged("list_user_ids", list_user_ids),
        "file_size": logged("file_size", file_size),
        "grep_count": logged("grep_count", grep_count),
        "get_product": logged("get_product", get_product),
        "to_upper": logged("to_upper", to_upper),
        "sum_list": logged("sum_list", sum_list),
        "now": logged("now", now),
    }


# Human-readable signatures shown to the model (the ONLY tool info it gets).
TOOL_SIGNATURES = {
    "list_files": "list_files(directory: str) -> list[str]  # paths of files in a directory",
    "read_file": "read_file(path: str) -> str  # full text contents of a file",
    "count_lines": "count_lines(text: str) -> int  # number of lines in a string",
    "parse_json": "parse_json(text: str) -> dict  # parse a JSON string into a dict",
    "get_user": "get_user(user_id: int) -> dict  # {'id','balance'}; raises if the id has no user",
    "get_orders": "get_orders(user_id: int) -> list[dict]  # each {'amount': int}; [] if none",
    "list_user_ids": "list_user_ids() -> list[int]  # all candidate user ids (some may not exist)",
    "file_size": "file_size(path: str) -> int  # size of a file in characters",
    "grep_count": "grep_count(pattern: str, text: str) -> int  # count of substring occurrences",
    "get_product": (
        "get_product(product_id: int) -> dict  # {'id','price','stock'}; raises if missing"
    ),
    "to_upper": "to_upper(s: str) -> str  # uppercase a string",
    "sum_list": "sum_list(nums: list[int]) -> int  # sum a list of numbers",
    "now": "now() -> int  # current unix timestamp",
}

# Full menu shown to HARD tasks (forces tool selection under distractors).
HARD_MENU = tuple(TOOL_SIGNATURES.keys())


# ---------------------------------------------------------------------------
# Frozen task set. Each task forces genuine multi-call orchestration (chaining,
# loops and/or conditionals). Descriptions are deliberately data-free.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    tools: tuple              # tool names exposed for this task
    required_tools: tuple     # must appear in the runtime call log
    min_calls: int            # minimum runtime tool calls for a legit solution
    needs_control: bool       # must use a loop OR conditional (or >=3 calls)
    reference: Callable[[], object]  # () -> expected result, computed from FIXTURE
    tier: str = "base"        # "base" (simple/moderate) | "hard" (21.2b stress tier)


def _ref_sum_lines():
    return sum(len(FIXTURE["files"][p].splitlines()) for p in FIXTURE["dirs"]["src"])


def _ref_find_todo():
    for p in FIXTURE["dirs"]["src"]:
        if "TODO" in FIXTURE["files"][p]:
            return p
    return None


def _ref_big_balance():
    return max(FIXTURE["users"].values(), key=lambda u: u["balance"])["id"]


def _ref_orders_over_10():
    return sum(o["amount"] for o in FIXTURE["orders"][42] if o["amount"] > 10)


def _ref_debug_plugins():
    cfg = json.loads(FIXTURE["files"]["config.json"])
    return cfg["plugins"] if cfg.get("debug") else []


def _ref_count_big_files():
    return sum(1 for p in FIXTURE["dirs"]["src"] if len(FIXTURE["files"][p].splitlines()) > 2)


TASKS = [
    Task(
        id="sum_lines",
        prompt=(
            "Read every file in the directory 'src' and compute the TOTAL number of "
            "lines across all of those files combined. Assign the integer total to `result`."
        ),
        tools=("list_files", "read_file", "count_lines"),
        required_tools=("list_files", "read_file"),
        min_calls=2, needs_control=True, reference=_ref_sum_lines,
    ),
    Task(
        id="find_todo",
        prompt=(
            "Find the FIRST file in directory 'src' whose contents contain the substring "
            "'TODO'. Assign that file's path to `result`, or assign None if no file contains it."
        ),
        tools=("list_files", "read_file"),
        required_tools=("list_files", "read_file"),
        min_calls=2, needs_control=True, reference=_ref_find_todo,
    ),
    Task(
        id="big_balance",
        prompt=(
            "Among the users with ids 1, 2 and 3, find the one with the HIGHEST balance. "
            "Assign that user's id to `result`."
        ),
        tools=("get_user",),
        required_tools=("get_user",),
        min_calls=3, needs_control=True, reference=_ref_big_balance,
    ),
    Task(
        id="orders_over_10",
        prompt=(
            "For the user with id 42, fetch their orders and compute the SUM of the amounts "
            "of all orders whose amount is strictly greater than 10. Assign that sum to `result`."
        ),
        tools=("get_orders",),
        required_tools=("get_orders",),
        min_calls=1, needs_control=True, reference=_ref_orders_over_10,
    ),
    Task(
        id="debug_plugins",
        prompt=(
            "Read the file 'config.json' and parse it as JSON. If its 'debug' field is true, "
            "assign the list of plugin names (its 'plugins' field) to `result`; otherwise assign "
            "an empty list to `result`."
        ),
        tools=("read_file", "parse_json"),
        required_tools=("read_file", "parse_json"),
        min_calls=2, needs_control=True, reference=_ref_debug_plugins,
    ),
    Task(
        id="count_big_files",
        prompt=(
            "Count how many files in directory 'src' have MORE THAN 2 lines. Assign that "
            "integer count to `result`."
        ),
        tools=("list_files", "read_file", "count_lines"),
        required_tools=("list_files", "read_file"),
        min_calls=2, needs_control=True, reference=_ref_count_big_files,
    ),
]


# --- HARD tier (item 21.2b): nesting, error-handling, transforms, and a LARGE
# tool menu (13 tools incl. distractors) so the model must select correctly. ---
def _ref_orders_gt10_count():
    return sum(1 for uid in (1, 2, 3) for o in FIXTURE["orders"][uid] if o["amount"] > 10)


def _ref_sum_existing_balances():
    total = 0
    for uid in FIXTURE["user_ids"]:
        if uid in FIXTURE["users"]:
            total += FIXTURE["users"][uid]["balance"]
    return total


def _ref_longest_file():
    return max(FIXTURE["dirs"]["src"], key=lambda p: len(FIXTURE["files"][p].splitlines()))


def _ref_long_plugins():
    cfg = json.loads(FIXTURE["files"]["config.json"])
    return [name for name in cfg["plugins"] if len(name) > 4]


def _ref_top2_balance_sum():
    bals = sorted((FIXTURE["users"][u]["balance"] for u in (1, 2, 3)), reverse=True)
    return bals[0] + bals[1]


HARD_TASKS = [
    Task(
        id="orders_gt10_count",
        prompt=(
            "For EACH of the users with ids 1, 2 and 3, fetch their orders. Count the TOTAL "
            "number of orders (across all three users) whose amount is strictly greater than 10. "
            "Assign that integer count to `result`."
        ),
        tools=HARD_MENU, required_tools=("get_orders",),
        min_calls=3, needs_control=True, reference=_ref_orders_gt10_count, tier="hard",
    ),
    Task(
        id="sum_existing_balances",
        prompt=(
            "Call list_user_ids() to get candidate user ids. Some ids may NOT correspond to a "
            "real user (get_user raises for those). Sum the balances of only the users that DO "
            "exist, skipping the ones that raise. Assign the total to `result`."
        ),
        tools=HARD_MENU, required_tools=("list_user_ids", "get_user"),
        min_calls=4, needs_control=True, reference=_ref_sum_existing_balances, tier="hard",
    ),
    Task(
        id="longest_file",
        prompt=(
            "Among the files in directory 'src', determine which file has the MOST lines. "
            "Assign that file's path to `result`."
        ),
        tools=HARD_MENU, required_tools=("list_files", "read_file"),
        min_calls=2, needs_control=True, reference=_ref_longest_file, tier="hard",
    ),
    Task(
        id="long_plugins",
        prompt=(
            "Read 'config.json' and parse it as JSON. From its 'plugins' list, collect every "
            "plugin name whose length is strictly greater than 4 characters, preserving order. "
            "Assign that list to `result`."
        ),
        tools=HARD_MENU, required_tools=("read_file", "parse_json"),
        min_calls=2, needs_control=True, reference=_ref_long_plugins, tier="hard",
    ),
    Task(
        id="top2_balance_sum",
        prompt=(
            "Among the users with ids 1, 2 and 3, find the TWO highest balances and assign their "
            "sum to `result`."
        ),
        tools=HARD_MENU, required_tools=("get_user",),
        min_calls=3, needs_control=True, reference=_ref_top2_balance_sum, tier="hard",
    ),
]

TASKS.extend(HARD_TASKS)
TASKS_BY_ID = {t.id: t for t in TASKS}


# ---------------------------------------------------------------------------
# Prompt construction — minimal, markdown-code-block (not JSON), per the survey.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You write a single Python program that solves a task by calling helper functions "
    "that are ALREADY DEFINED and in scope. Rules:\n"
    "- Output ONLY one Python code block, fenced as ```python ... ```. No prose, no explanation.\n"
    "- Do NOT redefine, import, or mock the helper functions; just call them.\n"
    "- Use only plain Python and the listed helpers "
    "(loops, conditionals, comprehensions are fine).\n"
    "- Assign your final answer to a variable named `result`.\n"
)


def build_user_prompt(task: Task) -> str:
    sigs = "\n".join("  " + TOOL_SIGNATURES[t] for t in task.tools)
    return (
        f"Available helper functions (already defined):\n{sigs}\n\n"
        f"Task: {task.prompt}\n\n"
        "Write the code block now."
    )


# ---------------------------------------------------------------------------
# Code extraction + static analysis
# ---------------------------------------------------------------------------
def extract_code(text: str):
    """Return (code, format_ok). Prefer a ```python fence; fall back to any ``` fence."""
    if not text:
        return "", False
    lines = text.splitlines()
    in_block = False
    fence_lang = None
    blocks = []
    cur: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("```"):
            if not in_block:
                in_block = True
                fence_lang = stripped[3:].strip().lower()
                cur = []
            else:
                blocks.append((fence_lang, "\n".join(cur)))
                in_block = False
        elif in_block:
            cur.append(ln)
    if in_block and cur:  # unterminated fence — be lenient
        blocks.append((fence_lang, "\n".join(cur)))
    if not blocks:
        return text.strip(), False  # no fence at all
    # prefer a python-tagged block, else the first block
    for lang, code in blocks:
        if lang in ("python", "py"):
            return code, True
    return blocks[0][1], True


def static_analysis(code: str):
    """Return dict: parse_ok, has_loop, has_cond, called_names (static)."""
    called: set[str] = set()
    out: dict[str, object] = {"parse_ok": False, "has_loop": False,
                              "has_cond": False, "called": called}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return out
    out["parse_ok"] = True
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While, ast.ListComp, ast.SetComp,
                             ast.DictComp, ast.GeneratorExp, ast.comprehension)):
            out["has_loop"] = True
        if isinstance(node, (ast.If, ast.IfExp)):
            out["has_cond"] = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    return out


# ---------------------------------------------------------------------------
# Execution engines
# ---------------------------------------------------------------------------
_SAFE_BUILTINS = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in (
        "len", "range", "sum", "min", "max", "sorted", "enumerate", "abs", "round",
        "any", "all", "list", "dict", "set", "tuple", "str", "int", "float", "bool",
        "zip", "map", "filter", "reversed", "True", "False", "None",
        # exception classes — needed so models' try/except error-handling code runs
        # (must match what Monty exposes, else exec unfairly fails error-handling tasks).
        "Exception", "BaseException", "KeyError", "ValueError", "TypeError",
        "IndexError", "FileNotFoundError", "KeyboardInterrupt", "StopIteration",
        "ZeroDivisionError", "AttributeError", "RuntimeError",
    )
    if (name in __builtins__ if isinstance(__builtins__, dict) else hasattr(__builtins__, name))
}


class _Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise _Timeout()


def run_exec(code: str, tools: dict, timeout_s: float = 5.0):
    """Restricted in-process sandbox. Returns (result, error_str)."""
    ns = dict(tools)
    ns["__builtins__"] = dict(_SAFE_BUILTINS)
    had_alarm = hasattr(signal, "SIGALRM")
    if had_alarm:
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        exec(compile(code, "<probe>", "exec"), ns)  # noqa: S102 — intentional sandboxed exec
        if "result" not in ns:
            return (None, "no `result` variable set")
        return (ns["result"], None)
    except _Timeout:
        return (None, "timeout")
    except Exception as e:  # noqa: BLE001 — any model bug is a graded failure, not a crash
        return (None, f"{type(e).__name__}: {e}")
    finally:
        if had_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)


_MONTY_UNAVAILABLE = object()


def run_monty(code: str, tools: dict, timeout_s: float = 5.0):
    """Faithful engine: execute the model's code inside the real Pydantic Monty VM.

    Monty (pydantic_monty v0.0.x, alpha) is a from-scratch Rust bytecode VM that runs
    a Python SUBSET. Host tools are passed as ``external_functions``; the model's code
    calls them as normal functions. We append ``\\nresult`` so Monty returns the value
    of the ``result`` variable (its run() returns the last expression).

    This is the engine that tests the survey's OPEN risk: does Monty's restricted
    dialect lower pass@1 vs full CPython ``exec``? A program that runs under exec but
    NOT under Monty (unsupported syntax/feature) fails here — that delta IS the signal.
    Returns (result, error_str); error_str is None on success.
    """
    try:
        import pydantic_monty as pm  # type: ignore
    except Exception:
        return (_MONTY_UNAVAILABLE, "monty-unavailable (pip install pydantic-monty)")
    program = code + "\nresult"
    limits = {"max_duration_secs": float(timeout_s), "max_recursion_depth": 200}
    try:
        m = pm.Monty(program)  # parse/compile — raises MontySyntaxError on dialect mismatch
    except Exception as e:  # noqa: BLE001
        return (None, f"{type(e).__name__}: {e}")
    try:
        # limits is a plain dict (works at runtime); Monty is an optional alpha dep
        # with a shifting typed API, so the precise ResourceLimits type isn't enforced.
        out = m.run(external_functions=dict(tools), limits=limits)  # type: ignore[arg-type]
        return (out, None)
    except Exception as e:  # noqa: BLE001 — runtime/dialect/timeout failures are graded, not raised
        msg = str(e)
        if "result" in msg and "not defined" in msg:
            return (None, "no `result` variable set")
        return (None, f"{type(e).__name__}: {msg}")


ENGINES = {"exec": run_exec, "monty": run_monty}


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------
@dataclass
class Grade:
    task: str
    sample: int
    format_ok: bool = False
    parse_ok: bool = False
    tools_ok: bool = False           # required tools actually CALLED at runtime
    orchestration_ok: bool = False   # real control flow OR >=3 chained calls
    exec_ok: bool = False            # ran without raising / timing out
    correct: bool = False            # result equals reference
    passed: bool = False             # the headline: all of the above
    fail_stage: str = ""             # first stage that failed (diagnostic)
    runtime_calls: int = 0
    error: str = ""
    raw_len: int = 0


def grade(task: Task, raw_text: str, engine, sample_idx: int) -> Grade:
    g = Grade(task=task.id, sample=sample_idx, raw_len=len(raw_text or ""))
    code, fmt = extract_code(raw_text)
    g.format_ok = fmt
    sa = static_analysis(code)
    g.parse_ok = sa["parse_ok"]
    if not sa["parse_ok"]:
        g.fail_stage = "parse" if fmt else "format"
        return g

    call_log: list[str] = []
    tools = _make_tools(call_log)
    # restrict the namespace to this task's exposed tools only
    exposed = {k: tools[k] for k in task.tools}
    result, err = engine(code, exposed)
    g.runtime_calls = len(call_log)
    g.exec_ok = err is None
    g.error = err or ""

    called = set(call_log)
    g.tools_ok = set(task.required_tools).issubset(called) and len(call_log) >= task.min_calls
    g.orchestration_ok = (sa["has_loop"] or sa["has_cond"] or g.runtime_calls >= 3) \
        if task.needs_control else True

    expected = task.reference()
    g.correct = g.exec_ok and (result == expected)

    g.passed = g.format_ok and g.parse_ok and g.tools_ok and g.orchestration_ok \
        and g.exec_ok and g.correct
    if not g.passed:
        for stage, ok in (("format", g.format_ok), ("parse", g.parse_ok),
                          ("exec", g.exec_ok), ("tools", g.tools_ok),
                          ("orchestration", g.orchestration_ok), ("correct", g.correct)):
            if not ok:
                g.fail_stage = stage
                break
    return g


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
def _http_models(base_url: str, timeout=10.0):
    url = base_url.rstrip("/") + "/models"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        obj = json.loads(r.read().decode())
    data = obj.get("data") or []
    if not data:
        raise RuntimeError("GET /v1/models returned no models")
    return data[0]["id"]


def http_complete(target: dict, system: str, user: str, timeout=180.0) -> str:
    base = target["base_url"].rstrip("/")
    model = target.get("model") or "auto"
    if model == "auto":
        model = _http_models(base)
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": target.get("temperature", 0.2),
        "max_tokens": target.get("max_tokens", 1024),
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    key_env = target.get("api_key_env")
    if key_env:
        key = os.environ.get(key_env)
        if not key:
            raise RuntimeError(f"target needs env var {key_env} (not set)")
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + "/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        obj = json.loads(r.read().decode())
    return obj["choices"][0]["message"]["content"] or ""


def opencode_complete(target: dict, system: str, user: str, timeout=300.0) -> str:
    """Drive `opencode run --format json -m MODEL` and pull the assistant text.

    For online models reachable only through opencode. opencode injects its own
    system prompt, so this is less controlled than http — the system text is
    prepended to the user message as a best effort.
    """
    model = target["model"]
    prompt = system + "\n\n" + user
    cmd = ["opencode", "run", "--format", "json", "-m", model, prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"opencode run failed: {proc.stderr[:400]}")
    text_parts = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = ev.get("part") or ev.get("properties", {}).get("part") or {}
        if part.get("type") == "text" and part.get("text"):
            text_parts.append(part["text"])
    return "\n".join(text_parts)


TRANSPORTS = {"http": http_complete, "opencode": opencode_complete}


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
DEFAULT_TARGETS = {
    "local-gemma": {
        "transport": "http",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "auto",
        "api_key_env": None,
        "temperature": 0.2,
        "max_tokens": 1024,
        "note": "Local Gemma-4-E4B QAT via the MLX repair proxy (:8080).",
    },
    "bigpickle": {
        "transport": "http",
        "base_url": "FILL_ME (OpenAI-compatible /v1 base url for the online model)",
        "model": "FILL_ME (model id) or 'auto'",
        "api_key_env": "BIGPICKLE_API_KEY",
        "temperature": 0.2,
        "max_tokens": 1024,
        "enabled": False,
        "note": ("Online control model — large frontier model to isolate whether SIZE is the "
                 "blocker. If reachable only via opencode, set transport='opencode' and "
                 "model='provider/model' and remove base_url/api_key_env."),
    },
}


def load_targets() -> dict:
    if os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE) as f:
            return json.load(f)
    return DEFAULT_TARGETS


def ensure_targets_file():
    if not os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE, "w") as f:
            json.dump(DEFAULT_TARGETS, f, indent=2)
        print(f"wrote default targets → {TARGETS_FILE}")


# ---------------------------------------------------------------------------
# Run / summary / selftest
# ---------------------------------------------------------------------------
def cmd_run(args) -> int:
    targets = load_targets()
    if args.target not in targets:
        print(f"unknown target {args.target!r}; configured: {', '.join(targets)}", file=sys.stderr)
        return 2
    target = targets[args.target]
    if target.get("enabled") is False:
        print(
            f"target {args.target!r} is disabled / unconfigured — edit {TARGETS_FILE}",
            file=sys.stderr,
        )
        return 2
    transport = TRANSPORTS.get(target.get("transport", "http"))
    if transport is None:
        print(f"unknown transport for target {args.target!r}", file=sys.stderr)
        return 2
    engines = args.engines
    if "monty" in engines:
        try:
            import pydantic_monty  # noqa: F401
        except Exception:
            print(
                "engine 'monty' needs pydantic-monty: `uv pip install pydantic-monty`",
                file=sys.stderr,
            )
            return 2

    if args.tasks:
        tasks = [TASKS_BY_ID[t] for t in args.tasks if t in TASKS_BY_ID]
    elif args.tier:
        tasks = [t for t in TASKS if t.tier == args.tier]
    else:
        tasks = list(TASKS)
    if not tasks:
        print("no valid tasks selected", file=sys.stderr)
        return 2
    task_ids = [t.id for t in tasks]

    # Generate ONCE per (task, sample); grade the SAME output through every engine
    # so exec-vs-monty is apples-to-apples (generation is the expensive step).
    # (task, sample_idx, raw_or_None, transport_err)
    raw_samples: list[tuple[Task, int, str | None, str | None]] = []
    for task in tasks:
        sys_p, usr_p = SYSTEM_PROMPT, build_user_prompt(task)
        for s in range(args.k):
            try:
                raw = transport(target, sys_p, usr_p)
                raw_samples.append((task, s, raw, None))
                print(f"  gen {task.id} #{s}: {len(raw)} chars")
            except (urllib.error.URLError, RuntimeError, subprocess.SubprocessError, OSError) as e:
                print(f"  ! {task.id} #{s}: transport error: {e}", file=sys.stderr)
                raw_samples.append((task, s, None, str(e)))

    rows = []
    for ename in engines:
        eng = ENGINES[ename]
        grades = []
        for task, s, raw_text, terr in raw_samples:
            if raw_text is None:
                grades.append(Grade(task=task.id, sample=s,
                                    error=f"transport: {terr}", fail_stage="transport"))
            else:
                grades.append(grade(task, raw_text, eng, s))
        n = len(grades)
        passed = sum(g.passed for g in grades)
        by_task = {}
        for t in tasks:
            gs = [g for g in grades if g.task == t.id]
            by_task[t.id] = {
                "pass_at_1": round(sum(x.passed for x in gs) / len(gs), 3),
                "pass_at_k": int(any(x.passed for x in gs)),
                "fail_stages": _stage_counts(gs),
            }
        by_tier = {}
        for tier in sorted({t.tier for t in tasks}):
            tg = [g for g in grades if TASKS_BY_ID[g.task].tier == tier]
            by_tier[tier] = round(sum(x.passed for x in tg) / len(tg), 3) if tg else 0.0
        stage_totals = _stage_counts(grades)
        row = {
            "ts": int(time.time()),
            "target": args.target,
            "model_hint": target.get("model"),
            "transport": target.get("transport", "http"),
            "engine": ename,
            "k": args.k,
            "tier": args.tier or "all",
            "tasks": task_ids,
            "n_samples": n,
            "pass_at_1": round(passed / n, 3) if n else 0.0,
            "by_tier": by_tier,
            "by_task": by_task,
            "fail_stages": stage_totals,
        }
        rows.append(row)
        with open(LEDGER, "a") as f:
            f.write(json.dumps(row) + "\n")

        print(f"\n=== {args.target} · engine={ename} · k={args.k} · tier={args.tier or 'all'} ===")
        print(f"OVERALL pass@1 = {row['pass_at_1']}  ({passed}/{n} samples)")
        print(f"by tier: {by_tier}")
        print(f"fail stages: {stage_totals}")
        for tid, d in by_task.items():
            tier = TASKS_BY_ID[tid].tier
            print(
                f"  [{tier:4s}] {tid:22s} pass@1={d['pass_at_1']:<5} "
                f"pass@{args.k}={d['pass_at_k']}  {d['fail_stages']}"
            )

    # dialect-cost readout: exec vs monty on the SAME generated outputs
    if len(rows) > 1:
        base = {r["engine"]: r["pass_at_1"] for r in rows}
        if "exec" in base and "monty" in base:
            delta = round(base["monty"] - base["exec"], 3)
            verdict = ("Monty dialect costs nothing here" if delta == 0 else
                       f"Monty dialect LOWERS pass@1 by {-delta}" if delta < 0 else
                       "Monty ran code exec rejected (looser)")
            print(
                f"\nMONTY-DIALECT READOUT: exec={base['exec']} vs monty={base['monty']}  "
                f"Δ={delta:+}  → {verdict}"
            )
    print(f"\nappended {len(rows)} row(s) → {LEDGER}")
    return 0


def _stage_counts(grades):
    out = {}
    for g in grades:
        if not g.passed and g.fail_stage:
            out[g.fail_stage] = out.get(g.fail_stage, 0) + 1
    return out


def cmd_summary(args) -> int:
    if not os.path.exists(LEDGER):
        print("no runs yet")
        return 0
    rows = []
    with open(LEDGER) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    # latest row per (target, engine, tier)
    latest = {}
    for r in rows:
        latest[(r["target"], r["engine"], r.get("tier", "all"))] = r
    print(
        f"{'target':16s} {'engine':6s} {'tier':5s} {'k':>3s} {'pass@1':>7s}  "
        "by_tier / fail_stages"
    )
    print("-" * 78)
    for (tgt, eng, tier), r in latest.items():
        print(f"{tgt:16s} {eng:6s} {tier:5s} {r['k']:>3} {r['pass_at_1']:>7}  "
              f"{r.get('by_tier', {})}  {r.get('fail_stages', {})}")
    # size-isolation: compare each non-local target to local-gemma at matching engine+tier
    print("\nSIZE-ISOLATION READOUT (is model size the blocker?):")
    shown = False
    for (tgt, eng, tier), r in latest.items():
        if tgt == "local-gemma":
            continue
        loc = latest.get(("local-gemma", eng, tier))
        if not loc:
            continue
        delta = round(loc["pass_at_1"] - r["pass_at_1"], 3)
        verdict = ("size IS likely the blocker" if delta < -0.2 else
                   "size NOT clearly the blocker" if abs(delta) <= 0.2 else
                   "local beats online (?)")
        print(
            f"  [{eng}/{tier}] local={loc['pass_at_1']} vs {tgt}={r['pass_at_1']}  "
            f"Δ={delta:+}  → {verdict}"
        )
        shown = True
    if not shown:
        print("  (need matching local-gemma + other-target rows at the same engine+tier)")
    return 0


# --- offline grader self-test (no model) -----------------------------------
_GOOD = {
    "sum_lines": (
        "```python\ntotal = 0\nfor p in list_files('src'):\n"
        "    total += count_lines(read_file(p))\nresult = total\n```"
    ),
    "find_todo": (
        "```python\nresult = None\nfor p in list_files('src'):\n"
        "    if 'TODO' in read_file(p):\n        result = p\n        break\n```"
    ),
    "big_balance": (
        "```python\nbest = None\nfor uid in [1, 2, 3]:\n    u = get_user(uid)\n"
        "    if best is None or u['balance'] > best['balance']:\n        best = u\n"
        "result = best['id']\n```"
    ),
    "orders_over_10": (
        "```python\nresult = sum(o['amount'] for o in get_orders(42) if o['amount'] > 10)\n```"
    ),
    "debug_plugins": (
        "```python\ncfg = parse_json(read_file('config.json'))\nif cfg['debug']:\n"
        "    result = cfg['plugins']\nelse:\n    result = []\n```"
    ),
    "count_big_files": (
        "```python\nresult = 0\nfor p in list_files('src'):\n"
        "    if count_lines(read_file(p)) > 2:\n        result += 1\n```"
    ),
}
# Hard-tier golden solutions (nesting, try/except error-handling, argmax, filter).
_GOOD_HARD = {
    "orders_gt10_count": (
        "```python\nresult = 0\nfor uid in [1, 2, 3]:\n    for o in get_orders(uid):\n"
        "        if o['amount'] > 10:\n            result += 1\n```"
    ),
    "sum_existing_balances": (
        "```python\ntotal = 0\nfor uid in list_user_ids():\n    try:\n"
        "        total += get_user(uid)['balance']\n    except Exception:\n        pass\n"
        "result = total\n```"
    ),
    "longest_file": (
        "```python\nbest = None\nbest_n = -1\nfor p in list_files('src'):\n"
        "    n = count_lines(read_file(p))\n    if n > best_n:\n        best_n = n\n"
        "        best = p\nresult = best\n```"
    ),
    "long_plugins": (
        "```python\ncfg = parse_json(read_file('config.json'))\n"
        "result = [name for name in cfg['plugins'] if len(name) > 4]\n```"
    ),
    "top2_balance_sum": (
        "```python\nbals = sorted([get_user(u)['balance'] for u in [1, 2, 3]], reverse=True)\n"
        "result = bals[0] + bals[1]\n```"
    ),
}
_BAD = {
    "hardcoded": ("sum_lines", "```python\nresult = 9\n```"),  # fail: tools/orchestration
    # parse failure: unclosed paren in the for-loop
    "syntax":    ("find_todo", "```python\nfor p in list_files('src'\n  result = p\n```"),
    # format + wrong: no code fence
    "no_fence":  ("orders_over_10", "result = sum(o['amount'] for o in get_orders(42))"),
    # correct? no: includes 5 -> 45 != 40
    "wrong_val": (
        "orders_over_10",
        "```python\nresult = sum(o['amount'] for o in get_orders(42))\n```",
    ),
    # crashes on missing id -> exec fail
    "no_skip":   (
        "sum_existing_balances",
        "```python\nresult = sum(get_user(u)['balance'] for u in list_user_ids())\n```",
    ),
}


def cmd_selftest(args) -> int:
    engine = ENGINES[getattr(args, "engine", "exec")]
    ok = True
    print(f"== positive cases — engine={getattr(args, 'engine', 'exec')} (should PASS) ==")
    for tid, code in {**_GOOD, **_GOOD_HARD}.items():
        g = grade(TASKS_BY_ID[tid], code, engine, 0)
        status = "PASS" if g.passed else f"FAIL@{g.fail_stage}({g.error})"
        print(
            f"  [{TASKS_BY_ID[tid].tier:4s}] {tid:22s} {status}  "
            f"calls={g.runtime_calls} correct={g.correct}"
        )
        ok = ok and g.passed
    print("== negative cases (should NOT pass) ==")
    for name, (tid, code) in _BAD.items():
        g = grade(TASKS_BY_ID[tid], code, engine, 0)
        rej = not g.passed
        print(f"  {name:12s} -> {'rejected@'+g.fail_stage if rej else 'WRONGLY PASSED'}")
        ok = ok and rej
    # reference sanity: expected values are what we think
    print("== reference expected values ==")
    for t in TASKS:
        print(f"  [{t.tier:4s}] {t.id:22s} = {t.reference()!r}")
    print("\nSELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def cmd_targets(args) -> int:
    ensure_targets_file()
    targets = load_targets()
    for name, t in targets.items():
        state = "disabled" if t.get("enabled") is False else "enabled"
        print(f"{name:16s} [{state}] transport={t.get('transport')} model={t.get('model')}")
        if t.get("note"):
            print(f"    {t['note']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Code-gen orchestration pass@1 probe (TODO 21.2)")
    sub = p.add_subparsers(dest="cmd", required=True)

    rn = sub.add_parser("run", help="run the probe against a target model")
    rn.add_argument("--target", required=True, help="target name from codegen_probe_targets.json")
    rn.add_argument("--k", type=int, default=5, help="samples per task (pass@1 = mean over k)")
    rn.add_argument("--tasks", nargs="*", default=None, help="subset of task ids (default: all)")
    rn.add_argument("--tier", choices=["base", "hard"], default=None,
                    help="only run tasks of this tier (ignored if --tasks given)")
    rn.add_argument("--engines", nargs="+", choices=list(ENGINES), default=["exec"],
                    help="execution sandbox(es): exec (restricted CPython) and/or monty (real "
                         "Pydantic Monty VM). Pass both to grade the same outputs through each.")
    rn.set_defaults(func=cmd_run)

    sm = sub.add_parser("summary", help="side-by-side pass@1 per target (latest run each)")
    sm.set_defaults(func=cmd_summary)

    st = sub.add_parser("selftest", help="offline grader self-test (no model)")
    st.add_argument("--engine", choices=list(ENGINES), default="exec",
                    help="grade golden solutions through this engine (exec|monty)")
    st.set_defaults(func=cmd_selftest)

    tg = sub.add_parser("targets", help="list/init configured targets")
    tg.set_defaults(func=cmd_targets)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
