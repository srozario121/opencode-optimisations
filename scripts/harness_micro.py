#!/usr/bin/env python3
"""Signal-producing micro-test harness for the local coding-agent stack (item 14).

Item 11's ``scripts/harness_eval.py`` scores opencode pass/fail over a SWE-bench
Lite subset, but on the local Gemma 4 E4B QAT model every config scores **0/8** —
a metric pinned at zero cannot rank harness levers. This instrument is the
**lower-bar, signal-producing** complement: it scores the **atomic capabilities**
the agentic loop is built from, each isolated and individually winnable, so the
aggregate **fractional** pass-rate sits off the 0-floor and *can* rank levers.

It is a SIBLING of ``harness_eval.py`` (resolved in plan-review): it imports that
module's reusable machinery — MLX server lifecycle / OOM-restart, deep-merge,
config hashing, and the shared ledger/summary helpers — and adds only the new
tiered-grading logic. ``harness_eval.py``'s SWE-bench path is untouched, and both
suites append to ONE unified ledger (``RunRow.suite`` discriminates; item-14 rows
carry per-tier + fractional fields, SWE-bench rows leave them empty).

Three tiers, all against a tiny FROZEN SYNTHETIC fixture tree committed under
``scripts/harness_micro_fixtures/`` (no SWE-bench dependency, no network, no
checkout — bounded under the OOM ceiling by construction):

  * **Tier 1 — single tool-call fidelity.** One test per exposed tool; graded on
    the *call* (well-formed, right tool, right params), not a downstream fix.
  * **Tier 2 — two-step tool sequences.** grep→read / glob→read / read→edit;
    graded on ordering AND the second call's dependence on the first's result.
  * **Tier 3 — micro-edits.** Tiny fully-specified one-line changes; graded on
    transcript AND filesystem state ("the file now contains X; nothing else
    changed").

Grading = **per-tier binary checks aggregated into a fractional score**. Each
check is one deterministic yes/no assertion on (a) the opencode tool-call
transcript — captured structured via ``opencode run --format json`` (the
``message.part.updated`` → ``part.type=="tool"`` events carry the tool name +
``input`` args) — and/or (b) a filesystem diff of the fixture copy. A tier's score
is ``checks_passed/checks_total``; the run headline is the fractional aggregate.

**Lever isolation.** Each config run materializes an ISOLATED opencode config dir
by cloning the real global ``~/.config/opencode`` (the current default harness =
``micro-baseline``) into a temp dir and applying the lever's deltas, then points
opencode at it via ``XDG_CONFIG_HOME``. The user's real config is never mutated.
The grown config-bundle schema (vs harness_eval's per-checkout bundle) can drive
all three named levers — system prompt, the coding-discipline skill, and the
custom read/grep tool ``.ts`` variants — see ``scripts/harness_micro_configs/``.

Usage:
  scripts/harness_micro.py run --config micro-baseline
  scripts/harness_micro.py run --config micro-terse-prompt --tests t1-read t1-grep
  scripts/harness_micro.py run --config micro-baseline --trace   # keep OTel on
  scripts/harness_micro.py summary
  scripts/harness_micro.py selftest                              # offline, no model

Exit codes: 0 ok · 2 usage/config (no suite, endpoint down, missing global config)
· 1 run error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_eval as he  # noqa: E402  (sibling module — reuse its machinery)

REPO_ROOT = he.REPO_ROOT
FIXTURES_DIR = os.path.join(REPO_ROOT, "scripts", "harness_micro_fixtures")
SUITE_MANIFEST = os.path.join(FIXTURES_DIR, "micro_suite.json")
MICRO_CONFIGS_DIR = os.path.join(REPO_ROOT, "scripts", "harness_micro_configs")
MICRO_TOOLS_DIR = os.path.join(REPO_ROOT, "scripts", "harness_micro_tools")
REPO_OC_TOOLS = os.path.join(REPO_ROOT, ".opencode", "tools")
GLOBAL_OC_DIR = os.path.expanduser(
    os.environ.get("OPENCODE_CONFIG_DIR", "~/.config/opencode"))
MICRO_RUNS_DIR = os.path.join(he.HARNESS_DIR, "micro-runs")

DEFAULT_TEST_TIMEOUT = 7 * 60     # per-test wall-clock cap (single short episode)
RULES_BASENAME = "mlx-gemma-rules.md"
SKILL_REL = os.path.join("skill", "coding-discipline", "SKILL.md")
HARNESS_PROMPT_FILE = "micro-system-prompt.md"


# --------------------------------------------------------------------------- #
# suite + config loading
# --------------------------------------------------------------------------- #
def load_suite() -> dict:
    if not os.path.exists(SUITE_MANIFEST):
        raise RuntimeError(f"micro suite manifest missing at {SUITE_MANIFEST}")
    with open(SUITE_MANIFEST) as f:
        return json.load(f)


def load_micro_config(name: str) -> dict:
    path = os.path.join(MICRO_CONFIGS_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise RuntimeError(f"micro config {name!r} not found at {path}")
    with open(path) as f:
        cfg = json.load(f)
    cfg.setdefault("name", name)
    return cfg


def suite_id(tests: list[dict]) -> str:
    ids = ",".join(sorted(t["id"] for t in tests))
    return hashlib.sha256(ids.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# isolated opencode config dir (clone global -> apply lever deltas)
# --------------------------------------------------------------------------- #
def _read_cap_env() -> dict:
    """Parse the generator's mlx-read-cap.env (KEY=VALUE) so the cloned baseline
    runs the read tool with the same caps the installed default uses."""
    path = os.path.join(GLOBAL_OC_DIR, "mlx-read-cap.env")
    out: dict = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def build_config_dir(cfg: dict, xdg_root: str, model_ref: str,
                     trace: bool) -> str:
    """Clone the global opencode config into ``xdg_root/opencode`` and apply the
    lever bundle. Returns the cloned opencode config dir. node_modules is
    symlinked (large); everything else is copied so deltas don't touch the real
    config. The user's ``~/.config/opencode`` is never modified.
    """
    if not os.path.isdir(GLOBAL_OC_DIR):
        raise RuntimeError(
            f"global opencode config {GLOBAL_OC_DIR} missing — run "
            f"`make mlx-up` / `scripts/mlx.sh opencode-config` first")
    cfg_dir = os.path.join(xdg_root, "opencode")
    shutil.copytree(
        GLOBAL_OC_DIR, cfg_dir,
        ignore=shutil.ignore_patterns("node_modules", "*.bak*", "opencode.jsonc"))
    nm_src = os.path.join(GLOBAL_OC_DIR, "node_modules")
    if os.path.isdir(nm_src):
        os.symlink(nm_src, os.path.join(cfg_dir, "node_modules"))

    oc_json = os.path.join(cfg_dir, "opencode.json")
    with open(oc_json) as f:
        data = json.load(f)

    # Drop the title-slot small model + OTel plugin for clean, fast grading runs.
    # (--title is passed to the episode so no title is ever generated; --trace
    #  keeps OTel on for the manual Jaeger trajectory pass.)
    if not trace:
        data.pop("plugin", None)
    data.pop("small_model", None)
    if isinstance(data.get("provider"), dict):
        data["provider"].pop("mlx-small", None)

    # Register the DETECTED served model id under the mlx-local provider, mirroring
    # harness_eval.apply_levers. The cloned global config may key the model under a
    # DIFFERENT path (config drift — the global config can lag the repo's current
    # serving path), so `-m mlx-local/<served>` would otherwise 404 with
    # ProviderModelNotFoundError. setdefault so an existing entry (and the clone's
    # baseURL) is preserved; only fill what's missing.
    served = model_ref.split("/", 1)[1] if "/" in model_ref else model_ref
    prov = data.setdefault("provider", {}).setdefault(he.DEFAULT_PROVIDER, {})
    prov.setdefault("npm", "@ai-sdk/openai-compatible")
    prov.setdefault("name", "Local MLX (Gemma 4 QAT)")
    prov.setdefault("options", {}).setdefault("baseURL", he.DEFAULT_BASE_URL)
    prov["options"].setdefault("apiKey", "not-needed")
    models = prov.setdefault("models", {})
    models.setdefault(served, {"limit": {"context": 32768, "output": 4096}})
    data["model"] = model_ref

    # Sampling lever — placed on the served model's options (opencode forwards a
    # custom-provider model's options into the OpenAI request; same path as
    # harness_eval.apply_levers).
    sampling = cfg.get("sampling") or {}
    if sampling:
        models[served].setdefault("options", {}).update(sampling)

    # Lever 1a — system prompt: a terse replacement for opencode's default build
    # prompt (per-agent prompt REPLACES, not appends — same as harness_eval).
    if cfg.get("system_prompt"):
        with open(os.path.join(cfg_dir, HARNESS_PROMPT_FILE), "w") as f:
            f.write(cfg["system_prompt"])
        agent = data.setdefault("agent", {}).setdefault("build", {})
        agent["prompt"] = f"{{file:./{HARNESS_PROMPT_FILE}}}"

    # Lever 1b — resident rules file (mlx-gemma-rules.md, layered via instructions).
    rules = cfg.get("rules")
    rules_path = os.path.join(cfg_dir, RULES_BASENAME)
    if isinstance(rules, dict):
        if rules.get("enabled") is False:
            data["instructions"] = [p for p in data.get("instructions", [])
                                    if not p.endswith(RULES_BASENAME)]
            if os.path.exists(rules_path):
                os.remove(rules_path)
        elif rules.get("content"):
            with open(rules_path, "w") as f:
                f.write(rules["content"])

    # Lever 2 — the coding-discipline skill (on/off + body wording). The cloned
    # tree already carries the default skill; override or remove per the bundle.
    skill = cfg.get("skill")
    skill_path = os.path.join(cfg_dir, SKILL_REL)
    if isinstance(skill, dict):
        if skill.get("enabled") is False:
            shutil.rmtree(os.path.dirname(skill_path), ignore_errors=True)
            sk = data.get("skills")
            if isinstance(sk, dict):
                sk["paths"] = [p for p in sk.get("paths", [])
                               if os.path.abspath(p) != os.path.abspath(
                                   os.path.join(GLOBAL_OC_DIR, "skill"))]
                if not sk["paths"]:
                    data.pop("skills", None)
        elif skill.get("body"):
            os.makedirs(os.path.dirname(skill_path), exist_ok=True)
            with open(skill_path, "w") as f:
                f.write(skill["body"])
    # Repoint any skills.paths that referenced the real global skill dir at the
    # clone's skill dir (so isolation holds even when the skill is kept as-is).
    sk = data.get("skills")
    if isinstance(sk, dict) and sk.get("paths"):
        sk["paths"] = [os.path.join(cfg_dir, "skill")
                       if os.path.abspath(p) == os.path.abspath(
                           os.path.join(GLOBAL_OC_DIR, "skill")) else p
                       for p in sk["paths"]]
    # Repoint instructions that referenced the real global rules file too.
    if data.get("instructions"):
        data["instructions"] = [
            rules_path if p.endswith(RULES_BASENAME) else p
            for p in data["instructions"]]

    # Lever 3 — custom tool .ts variant (read/grep description + param surface).
    variant = cfg.get("tools_variant", "default")
    if variant and variant != "default":
        vdir = os.path.join(MICRO_TOOLS_DIR, variant)
        if not os.path.isdir(vdir):
            raise RuntimeError(f"tools_variant {variant!r} not found at {vdir}")
        tools_dst = os.path.join(cfg_dir, "tools")
        os.makedirs(tools_dst, exist_ok=True)
        for fn in os.listdir(vdir):
            if fn.endswith(".ts"):
                shutil.copy2(os.path.join(vdir, fn), os.path.join(tools_dst, fn))

    # Raw escape hatch — deep-merge arbitrary opencode.json overrides last.
    data = he._deep_merge(data, cfg.get("opencode_config") or {})
    with open(oc_json, "w") as f:
        json.dump(data, f, indent=2)
    return cfg_dir


def episode_env(cfg: dict, xdg_root: str) -> dict:
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["XDG_CONFIG_HOME"] = xdg_root            # isolate opencode's global config
    env.update(_read_cap_env())                  # faithful read-cap defaults
    env.update({str(k): str(v) for k, v in (cfg.get("env") or {}).items()})
    return env


# --------------------------------------------------------------------------- #
# fixtures + episode
# --------------------------------------------------------------------------- #
def fixture_root() -> str:
    suite = load_suite()
    return os.path.join(FIXTURES_DIR, suite.get("fixture_root", "repo"))


def materialize_fixture(workdir: str) -> str:
    """Copy the pristine fixture tree into a fresh per-test workdir."""
    src = fixture_root()
    shutil.copytree(src, workdir)
    return workdir


def run_micro_episode(workdir: str, prompt: str, model_ref: str, env: dict,
                      run_dir: str, timeout: float, trace: bool) -> tuple[str, str, float]:
    """Drive opencode headlessly with structured JSON output. Returns
    (events_path, status, wall_s). status is "ok" | "timeout".

    ``--title micro`` skips the (slow) model-generated session title; ``--format
    json`` makes opencode emit the structured event stream we grade against.
    """
    os.makedirs(run_dir, exist_ok=True)
    events_path = os.path.join(run_dir, "events.json")
    log_path = os.path.join(run_dir, "opencode.log")
    cmd = ["opencode", "run", "--format", "json", "--title", "micro",
           "-m", model_ref, "--dir", workdir, prompt]
    t0 = time.perf_counter()
    with open(events_path, "w") as ev, open(log_path, "w") as log:
        try:
            proc = subprocess.run(cmd, env=env, stdout=ev, stderr=log,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return events_path, "timeout", time.perf_counter() - t0
    _ = proc
    return events_path, "ok", time.perf_counter() - t0


# --------------------------------------------------------------------------- #
# transcript parsing (opencode --format json -> tool calls)
# --------------------------------------------------------------------------- #
@dataclass
class ToolCall:
    tool: str
    input: dict
    status: str
    output: str


def _iter_events(text: str):
    """Yield event objects from opencode --format json output, tolerant of either
    a single JSON array or newline-delimited JSON (one event per line)."""
    text = text.strip()
    if not text:
        return
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            yield from obj
            return
        if isinstance(obj, dict):
            yield obj
            return
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _tool_part(event: dict) -> dict | None:
    """Extract a tool Part from an event, whatever the envelope shape."""
    if not isinstance(event, dict):
        return None
    if event.get("type") == "tool" and "tool" in event:
        return event
    props = event.get("properties")
    if isinstance(props, dict):
        part = props.get("part")
        if isinstance(part, dict) and part.get("type") == "tool":
            return part
    part = event.get("part")
    if isinstance(part, dict) and part.get("type") == "tool":
        return part
    return None


def parse_tool_calls(events_path: str) -> list[ToolCall]:
    """Parse the ordered list of tool calls from an events file. Multiple updates
    per callID (pending->running->completed) collapse to one call, keeping the
    richest input and latest status, preserving first-seen order."""
    try:
        with open(events_path) as f:
            text = f.read()
    except OSError:
        return []
    order: list[str] = []
    by_id: dict[str, ToolCall] = {}
    for event in _iter_events(text):
        part = _tool_part(event)
        if part is None:
            continue
        cid = part.get("callID") or part.get("id") or f"_{len(order)}"
        state = part.get("state") or {}
        inp = state.get("input")
        inp = inp if isinstance(inp, dict) else {}
        rec = by_id.get(cid)
        if rec is None:
            rec = ToolCall(tool=str(part.get("tool", "")), input={}, status="", output="")
            by_id[cid] = rec
            order.append(cid)
        if part.get("tool"):
            rec.tool = str(part["tool"])
        if inp:                       # keep the richest input seen
            rec.input = inp
        if state.get("status"):
            rec.status = str(state["status"])
        if state.get("output"):
            rec.output = str(state["output"])
    return [by_id[c] for c in order]


# --------------------------------------------------------------------------- #
# grading: per-check binary evaluators
# --------------------------------------------------------------------------- #
def _calls_to(calls: list[ToolCall], tool: str) -> list[ToolCall]:
    return [c for c in calls if c.tool.lower() == tool.lower()]


def _grep_line_numbers(calls: list[ToolCall]) -> list[int]:
    """Line numbers reported in any grep call's output (rg `file:line:match`)."""
    nums: list[int] = []
    for c in _calls_to(calls, "grep"):
        for line in (c.output or "").splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 2 and parts[1].strip().isdigit():
                nums.append(int(parts[1].strip()))
    return nums


def _as_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def eval_check(chk: dict, calls: list[ToolCall], workdir: str,
               pristine: str) -> bool:
    kind = chk.get("kind")

    if kind == "called":
        return bool(_calls_to(calls, chk["tool"]))

    if kind == "well_formed":
        # A structured call the runtime accepted: non-error status + a non-empty
        # input object. (Parameter correctness is graded by the arg_* checks.)
        return any(c.status != "error" and isinstance(c.input, dict) and c.input
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "arg_present":
        a = chk["arg"]
        return any(c.input.get(a) not in (None, "", [])
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "arg_int_equals":
        a, val = chk["arg"], int(chk["value"])
        return any(_as_int(c.input.get(a)) == val
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "arg_int_near":
        a, val, d = chk["arg"], int(chk["value"]), int(chk.get("max_delta", 2))
        return any((iv := _as_int(c.input.get(a))) is not None and abs(iv - val) <= d
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "arg_lte":
        a, val = chk["arg"], int(chk["value"])
        return any((iv := _as_int(c.input.get(a))) is not None and iv <= val
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "any_arg_contains":
        # Scoping check: any of the listed args on a call to `tool` contains value.
        args, val = chk["args"], chk["value"]
        return any(any(val in str(c.input.get(a, "")) for a in args)
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "call_count":
        n = len(_calls_to(calls, chk["tool"]))
        if "equals" in chk and n != int(chk["equals"]):
            return False
        if "max" in chk and n > int(chk["max"]):
            return False
        if "min" in chk and n < int(chk["min"]):
            return False
        return True

    if kind == "arg_contains":
        a, val = chk["arg"], chk["value"]
        return any(val in str(c.input.get(a, ""))
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "arg_contains_any":
        a, vals = chk["arg"], chk["values"]
        return any(any(v in str(c.input.get(a, "")) for v in vals)
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "arg_equals":
        a, val = chk["arg"], chk["value"]
        return any(str(c.input.get(a, "")) == val
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "arg_basename_equals":
        a, val = chk["arg"], chk["value"]
        return any(os.path.basename(str(c.input.get(a, ""))) == val
                   for c in _calls_to(calls, chk["tool"]))

    if kind == "order":
        first, then = chk["first"], chk["then"]
        seq = [c.tool.lower() for c in calls]
        if first.lower() not in seq or then.lower() not in seq:
            return False
        return seq.index(first.lower()) < _last_index(seq, then.lower())

    if kind == "read_offset_near_grep_line":
        delta = int(chk.get("max_delta", 15))
        lines = _grep_line_numbers(calls)
        if not lines:
            return False
        for c in _calls_to(calls, "read"):
            off = _as_int(c.input.get("offset"))
            if off is not None and any(abs(off - n) <= delta for n in lines):
                return True
        return False

    if kind == "file_contains":
        return chk["value"] in _read_file(os.path.join(workdir, chk["file"]))

    if kind == "file_equals":
        # Exact-content match, lenient only on trailing newlines.
        path = os.path.join(workdir, chk["file"])
        if not os.path.exists(path):
            return False
        return _read_file(path).rstrip("\n") == str(chk["value"]).rstrip("\n")

    if kind == "file_unchanged":
        # A named file must be byte-identical to the pristine fixture (collateral
        # guard: an edit elsewhere must not touch this file).
        return _read_file(os.path.join(workdir, chk["file"])) == _read_file(
            os.path.join(pristine, chk["file"]))

    if kind == "file_absent_substring":
        path = os.path.join(workdir, chk["file"])
        if not os.path.exists(path):
            return False           # the file must still exist to "not contain" X
        return chk["value"] not in _read_file(path)

    if kind == "only_changed":
        return _only_changed(workdir, pristine, set(chk["files"]))

    raise ValueError(f"unknown check kind: {kind!r}")


def _last_index(seq: list[str], val: str) -> int:
    for i in range(len(seq) - 1, -1, -1):
        if seq[i] == val:
            return i
    return -1


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _tree_files(root: str) -> set[str]:
    """Relative paths of all non-hidden, non-node_modules files under root."""
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "node_modules"]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if any(part.startswith(".") for part in rel.split(os.sep)):
                continue
            out.add(rel)
    return out


def _only_changed(workdir: str, pristine: str, expected: set[str]) -> bool:
    """True iff exactly ``expected`` differ between workdir and pristine."""
    wf, pf = _tree_files(workdir), _tree_files(pristine)
    changed: set[str] = set(wf ^ pf)               # added or removed
    for rel in wf & pf:
        if _read_file(os.path.join(workdir, rel)) != _read_file(
                os.path.join(pristine, rel)):
            changed.add(rel)
    return changed == expected


# --------------------------------------------------------------------------- #
# per-test + per-config scoring
# --------------------------------------------------------------------------- #
@dataclass
class TestResult:
    id: str
    tier: int
    status: str                    # "ok" | "timeout" | "oom" | "error:<…>"
    checks_passed: int
    checks_total: int
    checks: list[dict] = field(default_factory=list)   # [{name, passed}]
    wall_s: float = 0.0
    failure_category: str = ""     # item 17: shared taxonomy (he.classify_failure)


def grade_test(test: dict, calls: list[ToolCall], workdir: str,
               pristine: str) -> list[dict]:
    results = []
    for chk in test["checks"]:
        try:
            ok = eval_check(chk, calls, workdir, pristine)
        except Exception as e:  # noqa: BLE001 — a bad check fails closed, never aborts
            ok = False
            chk = {**chk, "_error": str(e)}
        results.append({"name": _check_name(chk), "passed": bool(ok)})
    return results


def _check_name(chk: dict) -> str:
    bits = [chk.get("kind", "?")]
    for k in ("tool", "arg", "value", "file", "first", "then"):
        if k in chk:
            bits.append(f"{k}={chk[k]}")
    return " ".join(bits)


def score_config(cfg: dict, tests: list[dict], model_ref: str, base_url: str,
                 label: str, timeout: float, trace: bool) -> list[TestResult]:
    pristine = fixture_root()
    results: list[TestResult] = []
    # One isolated config dir per config run, reused across all tests (the lever
    # bundle doesn't vary per test, and an identical system-prompt prefix lets the
    # MLX server's prefix cache warm after the first episode).
    xdg_root = os.path.join(MICRO_RUNS_DIR, label, "_xdg")
    shutil.rmtree(xdg_root, ignore_errors=True)
    os.makedirs(xdg_root, exist_ok=True)
    build_config_dir(cfg, xdg_root, model_ref, trace)
    env = episode_env(cfg, xdg_root)

    for i, test in enumerate(tests, 1):
        run_dir = os.path.join(MICRO_RUNS_DIR, label, test["id"])
        workdir = os.path.join(run_dir, "work")
        shutil.rmtree(workdir, ignore_errors=True)
        materialize_fixture(workdir)
        print(f"[{i}/{len(tests)}] {test['id']} (tier {test['tier']}) …", flush=True)
        try:
            events_path, status, wall = run_micro_episode(
                workdir, test["prompt"], model_ref, env, run_dir, timeout, trace)
        except Exception as e:  # noqa: BLE001
            results.append(TestResult(test["id"], test["tier"], f"error:{e}",
                                      0, len(test["checks"])))
            print(f"  -> ERROR {e}", file=sys.stderr)
            continue

        if status == "timeout" and not he.server_healthy(base_url):
            status = "oom"
        if not he.server_healthy(base_url):
            status = "oom"

        calls = parse_tool_calls(events_path)
        checks = grade_test(test, calls, workdir, pristine)
        cp = sum(1 for c in checks if c["passed"])
        ct = len(checks)
        tr = TestResult(test["id"], test["tier"], status, cp, ct,
                        checks=checks, wall_s=round(wall, 1))
        tr.failure_category = he.classify_failure(asdict(tr))   # item 17
        results.append(tr)
        print(f"  -> {cp}/{ct} checks  [{status}]  {round(wall,1)}s  "
              f"({len(calls)} tool calls)", flush=True)
        if status == "oom":
            he.restart_server(base_url)
    return results


def _aggregate(results: list[TestResult]) -> tuple[dict, int, int]:
    """Per-tier (passed, total) check counts + overall (passed, total)."""
    tiers: dict[str, list[int]] = {}
    cp_all = ct_all = 0
    for r in results:
        t = str(r.tier)
        cell = tiers.setdefault(t, [0, 0])
        cell[0] += r.checks_passed
        cell[1] += r.checks_total
        cp_all += r.checks_passed
        ct_all += r.checks_total
    return tiers, cp_all, ct_all


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    suite = load_suite()
    tests = suite["tests"]
    if args.tests:
        want = set(args.tests)
        tests = [t for t in tests if t["id"] in want]
        if not tests:
            print("error: none of the requested test ids are in the suite",
                  file=sys.stderr)
            return 2
    cfg = load_micro_config(args.config)

    if not he.server_healthy(args.base_url):
        print(f"MLX endpoint {args.base_url} is down — attempting restart …")
        if not he.restart_server(args.base_url):
            print(f"error: MLX endpoint {args.base_url} is down and restart "
                  f"failed — `make mlx-up` first", file=sys.stderr)
            return 2
    served = he.detect_model(args.base_url)
    model_ref = args.model or f"{he.DEFAULT_PROVIDER}/{served}"
    label = args.label or f"{cfg['name']}-{time.strftime('%Y%m%d-%H%M')}"

    print(f"Scoring micro config '{cfg['name']}' (hash {he.config_hash(cfg)})  "
          f"model={model_ref}  tests={len(tests)}\n")
    results = score_config(cfg, tests, model_ref, args.base_url, label,
                           args.timeout, args.trace)

    tiers, cp, ct = _aggregate(results)
    score = round(cp / ct, 4) if ct else 0.0
    row = he.RunRow(
        label=label, config_name=cfg["name"], config_hash=he.config_hash(cfg),
        model=model_ref, subset_id=suite_id(tests),
        sampling=cfg.get("sampling") or {}, timestamp=he._now_iso(),
        instances=[asdict(r) for r in results], passed=cp, total=ct,
        notes=cfg.get("description", ""), suite="micro",
        tiers={k: v for k, v in sorted(tiers.items())}, score=score,
        checks_passed=cp, checks_total=ct)
    he.append_ledger(row)
    tier_str = "  ".join(f"tier{k}={v[0]}/{v[1]}" for k, v in sorted(tiers.items()))
    print(f"\nconfig '{cfg['name']}': score {score}  ({cp}/{ct} checks)  {tier_str}")
    he.write_summary()
    print(f"Summary table -> {he.SUMMARY_MD}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    print(he.write_summary())
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Offline sanity checks for parsing + grading (no model needed)."""
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    # 1. event parsing — both array and NDJSON envelopes, mixed shapes
    ndjson = "\n".join([
        json.dumps({"type": "message.part.updated", "properties": {"part": {
            "type": "tool", "callID": "c1", "tool": "grep",
            "state": {"status": "completed", "input": {"pattern": "target_func"},
                      "output": "calc.py:20:def target_func(x):"}}}}),
        json.dumps({"type": "message.part.updated", "properties": {"part": {
            "type": "tool", "callID": "c2", "tool": "read",
            "state": {"status": "completed",
                      "input": {"filePath": "/x/calc.py", "offset": 18, "limit": 8},
                      "output": "..."}}}}),
    ])
    tmp = os.path.join(he.HARNESS_DIR, "_micro_selftest_events.json")
    os.makedirs(he.HARNESS_DIR, exist_ok=True)
    with open(tmp, "w") as f:
        f.write(ndjson)
    calls = parse_tool_calls(tmp)
    os.remove(tmp)
    check("parses two tool calls", len(calls) == 2)
    check("first call is grep", calls[0].tool == "grep")
    check("preserves richest input", calls[1].input.get("limit") == 8)

    # 2. transcript check kinds
    check("called grep", eval_check({"kind": "called", "tool": "grep"}, calls, "", ""))
    check("well_formed read",
          eval_check({"kind": "well_formed", "tool": "read"}, calls, "", ""))
    check("arg_contains pattern",
          eval_check({"kind": "arg_contains", "tool": "grep", "arg": "pattern",
                      "value": "target_func"}, calls, "", ""))
    check("arg_basename_equals filePath",
          eval_check({"kind": "arg_basename_equals", "tool": "read",
                      "arg": "filePath", "value": "calc.py"}, calls, "", ""))
    check("arg_present offset",
          eval_check({"kind": "arg_present", "tool": "read", "arg": "offset"},
                     calls, "", ""))
    check("order grep<read",
          eval_check({"kind": "order", "first": "grep", "then": "read"}, calls, "", ""))
    check("read_offset_near_grep_line (|18-20|<=15)",
          eval_check({"kind": "read_offset_near_grep_line", "max_delta": 15},
                     calls, "", ""))
    check("order false when reversed",
          not eval_check({"kind": "order", "first": "read", "then": "grep"},
                         calls, "", ""))
    # new precision / economy / scoping kinds
    check("arg_int_equals offset==18",
          eval_check({"kind": "arg_int_equals", "tool": "read", "arg": "offset",
                      "value": 18}, calls, "", ""))
    check("arg_int_near offset~20 (|18-20|<=3)",
          eval_check({"kind": "arg_int_near", "tool": "read", "arg": "offset",
                      "value": 20, "max_delta": 3}, calls, "", ""))
    check("arg_lte limit<=8",
          eval_check({"kind": "arg_lte", "tool": "read", "arg": "limit",
                      "value": 8}, calls, "", ""))
    check("arg_lte limit<=5 is false",
          not eval_check({"kind": "arg_lte", "tool": "read", "arg": "limit",
                          "value": 5}, calls, "", ""))
    check("any_arg_contains filePath~calc.py",
          eval_check({"kind": "any_arg_contains", "tool": "read",
                      "args": ["filePath"], "value": "calc.py"}, calls, "", ""))
    check("call_count read==1",
          eval_check({"kind": "call_count", "tool": "read", "equals": 1},
                     calls, "", ""))
    check("call_count read min 2 is false",
          not eval_check({"kind": "call_count", "tool": "read", "min": 2},
                         calls, "", ""))

    # 3. filesystem checks against a tmp pristine/work pair
    base = os.path.join(he.HARNESS_DIR, "_micro_selftest_fs")
    shutil.rmtree(base, ignore_errors=True)
    pristine = os.path.join(base, "pristine")
    work = os.path.join(base, "work")
    os.makedirs(pristine)
    with open(os.path.join(pristine, "config.py"), "w") as f:
        f.write("MAX_RETRIES = 3\n")
    shutil.copytree(pristine, work)
    with open(os.path.join(work, "config.py"), "w") as f:
        f.write("MAX_RETRIES = 5\n")
    check("file_contains new value",
          eval_check({"kind": "file_contains", "file": "config.py",
                      "value": "MAX_RETRIES = 5"}, calls, work, pristine))
    check("file_absent_substring old value",
          eval_check({"kind": "file_absent_substring", "file": "config.py",
                      "value": "MAX_RETRIES = 3"}, calls, work, pristine))
    check("only_changed target only",
          eval_check({"kind": "only_changed", "files": ["config.py"]},
                     calls, work, pristine))
    check("file_equals exact (newline-lenient)",
          eval_check({"kind": "file_equals", "file": "config.py",
                      "value": "MAX_RETRIES = 5"}, calls, work, pristine))
    check("file_unchanged false for edited file",
          not eval_check({"kind": "file_unchanged", "file": "config.py"},
                         calls, work, pristine))
    check("no_files_changed (only_changed []) false here",
          not eval_check({"kind": "only_changed", "files": []},
                         calls, work, pristine))
    with open(os.path.join(work, "stray.txt"), "w") as f:
        f.write("oops\n")
    check("only_changed false with stray file",
          not eval_check({"kind": "only_changed", "files": ["config.py"]},
                         calls, work, pristine))
    shutil.rmtree(base, ignore_errors=True)

    # 4. suite manifest + configs load and are well-formed
    suite = load_suite()
    check("suite has tests", bool(suite.get("tests")))
    check("every test has tier+prompt+checks",
          all({"tier", "prompt", "checks", "id"} <= set(t) for t in suite["tests"]))
    baseline_path = os.path.join(MICRO_CONFIGS_DIR, "micro-baseline.json")
    check("micro-baseline config present", os.path.exists(baseline_path))

    # 5. summary renders (shared helper; header names both suites)
    check("summary renders", "items 11 + 14" in he.write_summary())

    print(f"\nselftest: {'OK' if ok else 'FAILURES'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    rn = sub.add_parser("run", help="score the micro suite with a lever config")
    rn.add_argument("--config", required=True,
                    help="micro config name (scripts/harness_micro_configs/<name>.json)")
    rn.add_argument("--tests", nargs="+", help="limit to these test ids")
    rn.add_argument("--label", default=None, help="run label (default: config+timestamp)")
    rn.add_argument("--base-url", default=he.DEFAULT_BASE_URL)
    rn.add_argument("--model", default=None,
                    help="opencode model ref (default: mlx-local/<detected>)")
    rn.add_argument("--timeout", type=float, default=DEFAULT_TEST_TIMEOUT,
                    help="per-test wall-clock cap (s)")
    rn.add_argument("--trace", action="store_true",
                    help="keep the OTel plugin on (for the manual Jaeger pass)")
    rn.set_defaults(func=cmd_run)

    sm = sub.add_parser("summary", help="regenerate + print the markdown ledger table")
    sm.set_defaults(func=cmd_summary)

    st = sub.add_parser("selftest", help="offline sanity checks (no model needed)")
    st.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
