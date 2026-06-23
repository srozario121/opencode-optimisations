#!/usr/bin/env python3
"""Correctness-scoring harness for the local coding-agent stack (TODO item 11).

Drives **opencode** headlessly against the local Gemma 4 E4B QAT model (served
via mlx-lm on ``127.0.0.1``) over a tiny, frozen subset of SWE-bench Lite
instances and scores each one **pass/fail**, SWE-bench style: let the model edit
a checkout of the instance's repo at its base commit, then apply the instance's
*test* patch and run the designated tests. An instance passes iff every
``FAIL_TO_PASS`` test now passes and every ``PASS_TO_PASS`` test still passes.

This is the **correctness** instrument for item 11's harness-engineering
experiment. It is deliberately SEPARATE from ``scripts/mlx_bench.py`` (the
throughput-only instrument, items 9/10): throughput and task-success are
different measurements, so the two stay apart and ``mlx_bench.py`` is unchanged.

Why these design choices (all documented in docs/opencode-local.md):

  * **Native per-instance ``uv`` venvs, no Docker.** The official SWE-bench
    harness is Docker-based; Docker images are GBs each and awkward offline on a
    16 GB M1. Instead, ``prepare`` provisions one native venv per instance ONCE
    (online), verified by running the *gold* patch and confirming FAIL_TO_PASS
    flips. Those venvs + cloned repos are then frozen and reused fully offline.
    The curation screen (see ``prepare``) therefore picks instances that both
    install cleanly on macOS arm64 AND fit the ~30K-token context budget (so an
    episode never hits the ~40-50K Metal-OOM cliff — item-9 finding).
  * **Dataset via the HF datasets-server REST API** (urllib, stdlib only) — no
    ``datasets`` dependency; rows are cached locally on first fetch so runs are
    offline thereafter. Stdlib-only keeps this the sanctioned non-service
    ``scripts/`` shape: nothing imported by ``src/``.
  * **A "lever config" is an opencode-side override bundle** (opencode.json
    fragments + env vars + sampling params) materialized into the checkout for
    the run and restored afterwards. The model weights and serving engine stay
    FIXED (item-8 default); only the harness around them changes. Each config =
    one full run of the frozen subset, recorded in the ledger.
  * **Timeout / OOM = fail, then auto-recover.** Each instance has a hard
    wall-clock cap (default 10 min). On timeout OR a detected MLX-server crash
    mid-episode the instance scores **fail**, the reason is logged, the server
    is restarted via ``scripts/mlx.sh``, and the run continues. Slowness/OOM is
    a real harness deficiency, not an excuse to skip — scoring stays comparable.

The experiment tracker lives here too: every run appends a row to a JSONL ledger
and ``summary`` regenerates a human-readable markdown table (config -> score ->
delta vs baseline). The ledger captures enough to reproduce a run (config name +
hash, frozen-subset id, sampling params, model, date).

Layout (all under ``~/.config/opencode-optimisations/harness-eval/`` unless overridden):
  instances/<id>.json   cached SWE-bench Lite row (offline after first fetch)
  repos/<repo_slug>/    cloned source repo (checked out per-run to base_commit)
  envs/<id>/            per-instance uv venv (frozen after prepare)
  runs/<label>/<id>/    per-run artifacts (model patch, opencode log, test log)
  ledger.jsonl          append-only experiment ledger
  summary.md            regenerated markdown comparison table
The frozen subset MANIFEST and the lever CONFIGS are tracked in the repo
(scripts/harness_eval_subset.json, scripts/harness_configs/*.json) so the
experiment is reproducible from a clean checkout + a re-``prepare``.

Usage:
  scripts/harness_eval.py prepare --instances <id> [<id> ...]   # one-time, online
  scripts/harness_eval.py run --config baseline                 # score the subset
  scripts/harness_eval.py run --config low-temp --instances <id>
  scripts/harness_eval.py summary                               # markdown table
  scripts/harness_eval.py selftest                              # offline sanity

Exit codes: 0 ok · 2 usage/config (no subset, endpoint down, missing cache) ·
1 run error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MLX_SH = os.path.join(REPO_ROOT, "scripts", "mlx.sh")
SUBSET_MANIFEST = os.path.join(REPO_ROOT, "scripts", "harness_eval_subset.json")
CONFIGS_DIR = os.path.join(REPO_ROOT, "scripts", "harness_configs")

HARNESS_DIR = os.environ.get(
    "HARNESS_EVAL_DIR", os.path.expanduser("~/.config/opencode-optimisations/harness-eval")
)
INSTANCES_DIR = os.path.join(HARNESS_DIR, "instances")
REPOS_DIR = os.path.join(HARNESS_DIR, "repos")
ENVS_DIR = os.path.join(HARNESS_DIR, "envs")
RUNS_DIR = os.path.join(HARNESS_DIR, "runs")
LEDGER = os.path.join(HARNESS_DIR, "ledger.jsonl")
SUMMARY_MD = os.path.join(HARNESS_DIR, "summary.md")
TIER_REPORT = os.path.join(HARNESS_DIR, "tier-report.jsonl")  # item 17.5 structured

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_PROVIDER = "mlx-local"          # opencode provider id (docs/opencode-config.md)
HARNESS_PROMPT_FILE = ".harness_prompt.md"  # L3 prompt-replacement file (per-checkout)
DEFAULT_INSTANCE_TIMEOUT = 10 * 60      # hard per-instance wall-clock cap (s)
                                        # (item 16 / E1: 30→10 min — long episodes
                                        # are degenerate loops, not productive work)
DEFAULT_TEST_TIMEOUT = 15 * 60          # cap for the test phase alone (s)

# HF datasets-server REST endpoint — returns dataset rows as JSON over https,
# so we never need the `datasets` package. Cached per instance after first pull.
HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
SWEBENCH_LITE = ("princeton-nlp/SWE-bench_Lite", "default", "test")

# Rough chars/token ratio (same calibration as mlx_bench) to size the context
# pre-screen without a tokenizer. The problem_statement + a repo file budget is
# the dominant cost; this only gates curation, the server's count is truth.
CHARS_PER_TOKEN = 3.5
CONTEXT_SCREEN_TOKENS = 30_000          # subset pre-screen ceiling (item-11 spec)


# --------------------------------------------------------------------------- #
# small process helpers (stdlib only)
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], cwd: str | None = None, timeout: float | None = None,
         env: dict | None = None, capture: bool = True) -> subprocess.CompletedProcess:
    """Thin subprocess wrapper. Never raises on non-zero — callers inspect it."""
    return subprocess.run(
        cmd, cwd=cwd, timeout=timeout, env=env,
        capture_output=capture, text=True,
    )


def _git(args: list[str], cwd: str, timeout: float = 300.0) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd=cwd, timeout=timeout)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _slug(repo: str) -> str:
    return repo.replace("/", "__")


def _est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


# --------------------------------------------------------------------------- #
# dataset access (HF datasets-server REST -> local cache)
# --------------------------------------------------------------------------- #
def _http_get_json(url: str, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _ensure_dataset_cached(timeout: float = 120.0) -> None:
    """One-time bulk cache of every SWE-bench Lite row to instances/<id>.json.

    Pages the datasets-server ``/rows`` endpoint (offset/length) — the reliable
    view; the ``/filter`` view 500s/times-out intermittently. After this, every
    ``fetch_instance`` is a local read, so ``run`` is fully offline. Idempotent:
    skips rows already cached (presence of a sentinel marks completion).
    """
    sentinel = os.path.join(INSTANCES_DIR, "_dataset_cached")
    if os.path.exists(sentinel):
        return
    os.makedirs(INSTANCES_DIR, exist_ok=True)
    ds, cfg, split = SWEBENCH_LITE
    dsq = urllib.parse.quote(ds)
    offset, page = 0, 100
    total = None
    print("  caching SWE-bench Lite rows (one-time) …", flush=True)
    while total is None or offset < total:
        url = (f"{HF_ROWS_URL}?dataset={dsq}&config={cfg}&split={split}"
               f"&offset={offset}&length={page}")
        obj = _http_get_json(url, timeout=timeout)
        total = obj.get("num_rows_total", total)
        rows = obj.get("rows") or []
        if not rows:
            break
        for r in rows:
            row = r["row"]
            with open(os.path.join(INSTANCES_DIR, f"{row['instance_id']}.json"), "w") as f:
                json.dump(row, f, indent=2)
        offset += len(rows)
    with open(sentinel, "w") as f:
        f.write(_now_iso())
    print(f"  cached {offset} instances", flush=True)


def fetch_instance(instance_id: str, timeout: float = 120.0) -> dict:
    """Return the SWE-bench Lite row for ``instance_id``, caching it offline.

    Returns a cached copy without any network call (so every later ``run`` is
    offline). On a miss, bulk-caches the dataset once via ``/rows`` then reads.
    """
    cache = os.path.join(INSTANCES_DIR, f"{instance_id}.json")
    if not os.path.exists(cache):
        _ensure_dataset_cached(timeout=timeout)
    if not os.path.exists(cache):
        raise RuntimeError(
            f"instance {instance_id!r} not found in {SWEBENCH_LITE[0]} (check the id)")
    with open(cache) as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# manifest + config loading
# --------------------------------------------------------------------------- #
@dataclass
class InstanceSpec:
    """A frozen subset entry — what `prepare` records and `run` consumes."""
    instance_id: str
    repo: str
    base_commit: str
    test_cmd: str                  # how to invoke the tests (templated by curation)
    est_context_tokens: int        # pre-screen estimate (must be < CONTEXT_SCREEN_TOKENS)
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    prepared: bool = False         # venv provisioned + gold-patch verified
    notes: str = ""
    # --- item 17: static difficulty metadata (assigned offline by `tier`). ---
    # tier ∈ {3,4} on the unified ladder (T1/T2 are the synthetic micro-suite);
    # the rest are grouping signals derived from the gold patch + F2P set.
    tier: int = 0                  # 0 = un-bucketed (run `tier` to assign)
    n_files: int = 0               # non-test source files the gold patch touches
    needs_search: bool = False     # fix spans >1 edit site → must locate each
    needs_bash: bool = False       # task requires a shell tool (tests run externally)
    expected_tool_seq: list[str] = field(default_factory=list)  # advisory happy path


def load_subset() -> list[InstanceSpec]:
    if not os.path.exists(SUBSET_MANIFEST):
        return []
    with open(SUBSET_MANIFEST) as f:
        data = json.load(f)
    return [InstanceSpec(**e) for e in data.get("instances", [])]


def save_subset(specs: list[InstanceSpec], meta: dict | None = None) -> None:
    payload = {
        "_comment": ("Frozen SWE-bench Lite subset for TODO item 11 harness eval. "
                     "Each instance pre-screened under "
                     f"{CONTEXT_SCREEN_TOKENS} est. tokens and verified by running "
                     "its gold patch (FAIL_TO_PASS flips). Regenerate with "
                     "scripts/harness_eval.py prepare."),
        "context_screen_tokens": CONTEXT_SCREEN_TOKENS,
        "frozen_at": (meta or {}).get("frozen_at", _now_iso()),
        "instances": [asdict(s) for s in specs],
    }
    os.makedirs(os.path.dirname(SUBSET_MANIFEST), exist_ok=True)
    with open(SUBSET_MANIFEST, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote frozen subset ({len(specs)} instances) -> {SUBSET_MANIFEST}")


def load_config(name: str) -> dict:
    """Load a lever config bundle from scripts/harness_configs/<name>.json.

    Shape (all keys optional except name):
      { "name": str, "description": str,
        "opencode_config": {…},   # merged into the checkout's opencode.json
        "env": {KEY: VALUE},       # extra env for the opencode run
        "sampling": {"temperature": .., "top_p": .., "top_k": ..},
        "system_prompt": str|null } # AGENTS.md content override for the checkout
    """
    path = os.path.join(CONFIGS_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise RuntimeError(f"config {name!r} not found at {path}")
    with open(path) as f:
        cfg = json.load(f)
    cfg.setdefault("name", name)
    return cfg


def config_hash(cfg: dict) -> str:
    """Stable short hash of the lever bundle (for reproducibility in the ledger)."""
    blob = json.dumps({k: cfg[k] for k in sorted(cfg) if k != "description"},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# MLX server lifecycle (for OOM/crash recovery)
# --------------------------------------------------------------------------- #
def server_healthy(base_url: str, timeout: float = 8.0) -> bool:
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode())
        return bool(obj.get("data"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def detect_model(base_url: str, timeout: float = 10.0) -> str:
    obj = _http_get_json(base_url.rstrip("/") + "/models", timeout=timeout)
    data = obj.get("data") or []
    if not data:
        raise RuntimeError("GET /v1/models returned no models")
    return data[0].get("id", "unknown")


def restart_server(base_url: str, wait_s: float = 180.0) -> bool:
    """Bounce the MLX stack via scripts/mlx.sh and wait for health. Returns ok."""
    print("  [recover] restarting MLX server via scripts/mlx.sh …", flush=True)
    _run(["bash", MLX_SH, "down"], timeout=120)
    _run(["bash", MLX_SH, "up"], timeout=600)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if server_healthy(base_url):
            print("  [recover] server healthy again", flush=True)
            return True
        time.sleep(3)
    print("  [recover] server did NOT come back healthy", file=sys.stderr)
    return False


# --------------------------------------------------------------------------- #
# checkout + lever materialization
# --------------------------------------------------------------------------- #
def clean_checkout(spec: InstanceSpec) -> str:
    """Reset repos/<slug> to base_commit, discarding any prior-run edits."""
    repo_dir = os.path.join(REPOS_DIR, _slug(spec.repo))
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        raise RuntimeError(
            f"repo cache missing for {spec.repo} at {repo_dir} — run `prepare`")
    _git(["clean", "-xffd"], cwd=repo_dir)
    _git(["reset", "--hard"], cwd=repo_dir)
    co = _git(["checkout", "-f", spec.base_commit], cwd=repo_dir)
    if co.returncode != 0:
        raise RuntimeError(
            f"git checkout {spec.base_commit} failed: {co.stderr.strip()}")
    return repo_dir


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_levers(checkout: str, cfg: dict, model_ref: str, base_url: str) -> dict:
    """Materialize a lever bundle into the checkout. Returns the run env.

    Writes a project ``opencode.json`` (provider + model limits + any
    opencode_config overrides + sampling under the model options) and, if the
    config carries a ``system_prompt``, an ``AGENTS.md`` (opencode's project
    rules file — docs/opencode-config.md). The checkout is reset between
    instances by clean_checkout, so these files never leak across runs.

    Sampling note: opencode forwards a custom-provider model's ``options`` into
    the OpenAI request; temperature/top_p/top_k are placed there. The whole
    ``sampling`` block is copied verbatim, so any key mlx-lm's server reads from
    the request body (incl. anti-repetition: ``repetition_penalty`` /
    ``repetition_context_size`` / ``presence_penalty`` / ``frequency_penalty`` —
    verified present in mlx-lm 0.31.3 server.py) flows through. NOTE:
    ``no_repeat_ngram_size`` is NOT supported by mlx-lm's server and is silently
    dropped — L1 anti-repetition must use ``repetition_penalty`` instead.
    This forwarding path is version-sensitive (docs/opencode-config.md);
    ``selftest --check-sampling`` asserts the block lands in the written
    opencode.json model options. Whether opencode's openai-compatible provider
    actually serialises arbitrary keys into the wire request body is the one
    [needs-live-verification] link — confirm via the repair-proxy request log on
    the first L0/L1 run.
    """
    served = model_ref.split("/", 1)[1] if "/" in model_ref else model_ref
    model_opts: dict = {"limit": {"context": 32768, "output": 4096}}
    sampling = cfg.get("sampling") or {}
    if sampling:
        model_opts["options"] = dict(sampling)
    base_conf = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            DEFAULT_PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Local MLX (Gemma 4 QAT)",
                "options": {"baseURL": base_url, "apiKey": "not-needed"},
                "models": {served: model_opts},
            }
        },
        "model": model_ref,
        "small_model": model_ref,
    }
    conf = _deep_merge(base_conf, cfg.get("opencode_config") or {})
    # L3 (terser prompt): a custom agent `prompt` REPLACES opencode's default
    # system prompt (it is not appended — see docs/harness-engineering-research.md
    # §1). We write it to a file in the checkout and point the default `build`
    # agent at it (unless the config already set an agent prompt explicitly).
    if cfg.get("system_prompt"):
        with open(os.path.join(checkout, HARNESS_PROMPT_FILE), "w") as f:
            f.write(cfg["system_prompt"])
        agent = conf.setdefault("agent", {}).setdefault("build", {})
        agent.setdefault("prompt", f"{{file:./{HARNESS_PROMPT_FILE}}}")
    with open(os.path.join(checkout, "opencode.json"), "w") as f:
        json.dump(conf, f, indent=2)
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env.update({str(k): str(v) for k, v in (cfg.get("env") or {}).items()})
    return env


# --------------------------------------------------------------------------- #
# E0 — per-episode metrics (item 16). The adopt/reject signal: full pass/fail
# (0/8) gives no gradient, so we emit per-episode intermediate signals.
#
# Source = `opencode run --format json` (verified 2026-06-23 against the live
# Gemma-4-E4B/MLX stack). It is NDJSON, one event per line:
#     {"type","timestamp","sessionID","part":{...}}
#   step_start   part:{type:"step-start", id, ...}                  — a loop step
#   tool_use     part:{type:"tool", tool, callID, state:{status, input,
#                      output, time:{start,end}}}                   — one tool call
#   step_finish  part:{reason, tokens:{output,total,...}, cost}     — step end
#   text         part:{text, time, synthetic?}                      — assistant text
# `--format json` emits FINAL parts (no deltas) but BUFFERS to EOF, so a killed
# (timed-out) episode leaves an EMPTY file — exactly the degenerate case we most
# want to measure. The streaming `--print-logs` stderr (`message=loop step=N`,
# `message="exiting loop"`) is the real-time E2 heartbeat source AND the fallback
# metric source: on a json-less timeout we synthesize coarse metrics from it
# (step count + stuck-until-cap ⇒ degenerate). See run_opencode_episode.
# --------------------------------------------------------------------------- #
EDIT_TOOLS = {"edit", "write", "patch", "multiedit"}
DEGENERATE_MIN_REPEATS = 6           # >= identical normalized text lines = degenerate
HEARTBEAT_EVERY_S = 30               # E2: emit a progress line at least this often
_WS_RE = re.compile(r"\s+")
_LOOP_STEP_RE = re.compile(r"message=loop\b.*\bstep=(\d+)")


# --------------------------------------------------------------------------- #
# item 17: the unified 4-tier difficulty ladder + the shared failure taxonomy
# --------------------------------------------------------------------------- #
# One ladder spans BOTH harnesses (decision A — unify): the synthetic micro-suite
# (harness_micro.py) supplies the easy, passable rungs a weak model can clear; the
# real SWE-bench fixes supply the hard rungs. This gives the gradient item 16
# found missing (the 8 sympy instances were all hard → a flat 0/8, no signal).
GLOBAL_TIERS: dict[int, str] = {
    1: "single tool-call fidelity (synthetic micro-suite)",
    2: "multi-step sequence + micro-edit (synthetic micro-suite)",
    3: "single-file real bug-fix, localized (SWE-bench)",
    4: "multi-file/multi-site real bug-fix + reasoning (SWE-bench)",
}
# micro-suite local tier (its own 1/2/3) → global ladder tier. Its tier-1 single
# call → T1; its tier-2 (two-step) and tier-3 (micro-edit) both → T2.
MICRO_TIER_MAP: dict[int, int] = {1: 1, 2: 2, 3: 2}

# The shared `failure_category` vocabulary. The first seven are item-16's defect
# taxonomy (the harness/tool-reliability modes 17/18 also target); the trailing
# three are non-defect terminal outcomes so the histogram is faithful (a wrong-but-
# clean fix is model capability, not a harness defect — item 16's standing note).
# Order is the classification PRECEDENCE (most specific / most severe first).
FAILURE_CATEGORIES: list[str] = [
    "oom",               # server crashed mid-episode (Metal OOM)
    "degenerate-loop",   # repeated planning sentence / stuck-until-cap (item 16)
    "timeout",           # hit the wall-clock cap without a degenerate signature
    "no-edit",           # spent the turn but produced no patch (incl. dropped-output)
    "edit-mismatch",     # an edit/patch call failed to apply (L3 territory)
    "grep-parse-error",  # a search call errored (L2 territory)
    "catastrophic-edit", # edited but REGRESSED a previously-passing test (P2P broke)
    "tests-failed",      # edited cleanly but the fix is wrong (F2P didn't flip)
    "error",             # harness-level exception scoring the instance
    "ok",                # passed (not a failure — present so the histogram sums to n)
]


def _swebench_category(inst: dict) -> str:
    """Map one scored SWE-bench instance dict → a `failure_category`.

    Derived from the terminal `reason` + the E0 metric block (decision B —
    observed outcome, not a static tag). Precedence follows FAILURE_CATEGORIES:
    a more specific signal (degenerate loop, OOM) wins over a coarser one.
    """
    if inst.get("passed"):
        return "ok"
    reason = inst.get("reason", "") or ""
    m = inst.get("metrics") or {}
    if reason == "oom":
        return "oom"
    if m.get("degenerate_loop"):
        return "degenerate-loop"
    if reason == "timeout":
        return "timeout"
    if reason == "no-edit":
        return "no-edit"
    if reason == "apply-failed":
        return "edit-mismatch"
    # A search tool that errored without any edit landing (L2 signal). errored_tools
    # is recorded by parse_episode_jsonl; absent on the stderr-fallback path.
    errored = m.get("errored_tools") or []
    if any(t in ("grep", "glob") for t in errored) and not m.get("made_edit"):
        return "grep-parse-error"
    if any(t in EDIT_TOOLS for t in errored) and not m.get("made_edit"):
        return "edit-mismatch"
    if reason == "tests-failed":
        # P2P regression ⇒ the edit broke working code (catastrophic); otherwise the
        # fix is merely wrong (model capability, not a harness defect).
        if inst.get("pass_to_pass_passed", 0) < inst.get("pass_to_pass_total", 0):
            return "catastrophic-edit"
        return "tests-failed"
    if reason.startswith("error"):
        return "error"
    return reason or "error"


def _micro_category(inst: dict) -> str:
    """Map one micro-suite TestResult dict → a `failure_category`.

    Micro tests are synthetic tool-call fidelity probes (no real test flips), so
    only the runtime statuses + an all-checks-pass outcome carry over to the shared
    vocabulary. A partial-check miss on an edit tier is an `edit-mismatch`; on a
    non-edit tier it is a tool-call fidelity miss (`no-edit` — the call the task
    asked for never landed correctly)."""
    status = inst.get("status", "") or ""
    if status == "oom":
        return "oom"
    if status == "timeout":
        return "timeout"
    if status.startswith("error"):
        return "error"
    cp, ct = inst.get("checks_passed", 0), inst.get("checks_total", 0)
    if ct and cp == ct:
        return "ok"
    # A miss on the micro edit tier (local tier 3) is an edit fidelity failure; a
    # miss on the call/sequence tiers is the asked-for call never landing.
    return "edit-mismatch" if inst.get("tier") == 3 else "no-edit"


def classify_failure(inst: dict) -> str:
    """Shared failure_category for one ledger instance, either suite.

    Dispatches on the row shape: SWE-bench instances carry `reason`; micro tests
    carry `status` + `checks_total`. Returns a member of FAILURE_CATEGORIES.
    """
    if "reason" in inst:
        return _swebench_category(inst)
    return _micro_category(inst)


def _manifest_tier_map() -> dict[str, int]:
    """{instance_id: assigned tier} from the frozen subset (cached per process).

    Lets historical ledger rows — written before tiers were recorded on the row —
    still classify correctly at report time. The manifest is the source of truth;
    a row's own frozen `tier` (if present) takes precedence over this fallback."""
    cache = getattr(_manifest_tier_map, "_cache", None)
    if cache is None:
        cache = {s.instance_id: s.tier for s in load_subset() if s.tier in (3, 4)}
        _manifest_tier_map._cache = cache       # type: ignore[attr-defined]
    return cache


def instance_tier(inst: dict, suite: str) -> int:
    """Global ladder tier (1-4) for one ledger instance.

    Micro tests map their local tier through MICRO_TIER_MAP. SWE-bench instances
    use their frozen-on-row `tier`; older rows that lack it fall back to the subset
    manifest by instance_id, then to T3 if still unknown.
    """
    if suite == "micro":
        return MICRO_TIER_MAP.get(int(inst.get("tier", 0)), 2)
    t = int(inst.get("tier", 0) or 0)
    if t in (3, 4):
        return t
    return _manifest_tier_map().get(inst.get("instance_id", ""), 3)


def _max_line_repeat(texts: list[str]) -> int:
    """Max repeat count of any non-trivial normalized line across assistant text.

    The item-16 degenerate signature is the model repeating the same planning
    sentence many times; those repeats are newline-separated in its output.
    """
    counts: dict[str, int] = {}
    for t in texts:
        for line in t.splitlines():
            nl = _WS_RE.sub(" ", line.strip()).lower()
            if len(nl) >= 12:           # ignore blank / trivial lines
                counts[nl] = counts.get(nl, 0) + 1
    return max(counts.values(), default=1)


def parse_episode_jsonl(path: str) -> dict:
    """Parse the buffered `--format json` NDJSON into the E0 metric block.

    Returns {} if the file is missing/empty (the timed-out / crashed case — the
    caller then falls back to the stderr-derived metrics).
    """
    try:
        with open(path) as f:
            lines = [ln for ln in f if ln.strip()]
    except OSError:
        return {}
    if not lines:
        return {}
    steps = tool_calls = tool_errors = output_tokens = tool_call_rounds = 0
    first_evt_ts = first_tool_ts = None
    first_tool_step = steps_to_first_edit = None
    made_edit = False
    texts: list[str] = []
    seen_tools: set = set()
    errored_tools: list[str] = []      # item 17: tool names that returned an error
    for raw in lines:
        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ts = evt.get("timestamp")
        if isinstance(ts, (int, float)) and first_evt_ts is None:
            first_evt_ts = ts
        etype = evt.get("type")
        part = evt.get("part") or {}
        if etype == "step_start":
            steps += 1
        elif etype == "step_finish":
            toks = (part.get("tokens") or {}).get("output")
            if isinstance(toks, int):
                output_tokens += toks
            # `step_finish.reason == "tool-calls"` is the ROBUST tool-activity
            # signal: verified 2026-06-23 that `tool_use` events can be absent
            # even when tool calls happened (e.g. sympy-21627: 8 tool-call rounds,
            # 0 tool_use events). tool_calls (below) is the detail count and may
            # undercount; tool_call_rounds is the reliable "did it act" measure.
            if part.get("reason") == "tool-calls":
                tool_call_rounds += 1
        elif etype == "text":
            txt = part.get("text")
            if txt and not part.get("synthetic"):
                texts.append(txt)
        elif etype == "tool_use":
            cid = part.get("callID") or part.get("id")
            if cid in seen_tools:
                continue
            seen_tools.add(cid)
            tool_calls += 1
            if (part.get("state") or {}).get("status") == "error":
                tool_errors += 1
                tname = part.get("tool")
                if tname:
                    errored_tools.append(tname)
            if first_tool_step is None:
                first_tool_step = steps
                if isinstance(ts, (int, float)):
                    first_tool_ts = ts
            if part.get("tool", "") in EDIT_TOOLS:
                made_edit = True
                if steps_to_first_edit is None:
                    steps_to_first_edit = steps
    offset_s = None
    if first_tool_ts is not None and first_evt_ts is not None:
        offset_s = round((first_tool_ts - first_evt_ts) / 1000.0, 1)
    max_repeat = _max_line_repeat(texts)
    # `dropped_output` = the model spent output tokens but opencode rendered
    # NEITHER assistant text NOR any tool activity — i.e. the turn produced
    # nothing usable and the agent loop stopped. Verified 2026-06-23 as the
    # DOMINANT baseline mode (3/8: 142-302 tok → no text, no tool): the
    # tool-call-reliability floor, almost certainly a malformed first tool call
    # the repair proxy didn't fix. NOT addressed by L1-L5.
    has_tool = tool_calls > 0 or tool_call_rounds > 0
    return {
        "source": "json",
        "steps": steps,
        "tool_call_rounds": tool_call_rounds,
        "tool_calls": tool_calls,
        "tool_calls_error": tool_errors,
        "errored_tools": errored_tools,
        "dropped_output": output_tokens > 0 and not texts and not has_tool,
        "output_tokens": output_tokens,
        "made_edit": made_edit,
        "steps_to_first_edit": steps_to_first_edit,
        "first_tool_step": first_tool_step,
        "first_tool_offset_s": offset_s,
        "max_line_repeat": max_repeat,
        "degenerate_loop": max_repeat >= DEGENERATE_MIN_REPEATS,
    }


# --------------------------------------------------------------------------- #
# the opencode episode + scoring
# --------------------------------------------------------------------------- #
PROMPT_TEMPLATE = (
    "You are fixing a bug in the {repo} repository. Resolve the following issue "
    "by editing the source files in this repository. Do NOT edit any test "
    "files; the tests are fixed externally. When done, make sure your code "
    "changes are saved to disk.\n\n--- ISSUE ---\n{problem}\n"
)


@dataclass
class InstanceResult:
    instance_id: str
    passed: bool
    reason: str                    # "ok" | "tests-failed" | "timeout" | "oom" |
                                   # "no-edit" | "apply-failed" | "error:<…>"
    episode_wall_s: float
    test_wall_s: float
    model_patch_bytes: int
    fail_to_pass_passed: int
    fail_to_pass_total: int
    pass_to_pass_passed: int
    pass_to_pass_total: int
    metrics: dict = field(default_factory=dict)   # E0 per-episode signals (item 16)
    tier: int = 0                  # item 17: global ladder tier frozen from the spec
    failure_category: str = ""     # item 17: derived terminal mode (shared taxonomy)


def run_opencode_episode(checkout: str, spec: InstanceSpec, model_ref: str,
                         env: dict, run_dir: str,
                         timeout: float) -> tuple[str, float, dict]:
    """Drive opencode headlessly in the checkout. Returns (status, wall_s, metrics).

    status is "ok" on a clean exit, "timeout" if the per-instance cap is hit.
    Runs with ``--format json`` (→ run_dir/opencode.jsonl, the E0 source) and
    ``--print-logs --log-level INFO`` (→ run_dir/opencode.log, streamed in real
    time for the E2 heartbeat). stdout (json) is buffered by opencode and only
    flushed at EOF, so a killed/timed-out episode leaves opencode.jsonl empty —
    in that case E0 metrics are synthesized from the streamed stderr.
    """
    prompt = PROMPT_TEMPLATE.format(repo=spec.repo, problem=spec.problem)  # type: ignore[attr-defined]
    cmd = ["opencode", "run", "--format", "json", "--print-logs",
           "--log-level", "INFO", "-m", model_ref, "--dir", checkout, prompt]
    os.makedirs(run_dir, exist_ok=True)
    jsonl_path = os.path.join(run_dir, "opencode.jsonl")
    log_path = os.path.join(run_dir, "opencode.log")
    iid = spec.instance_id
    t0 = time.perf_counter()
    status = "ok"
    stderr_steps = -1          # highest `loop step=N` seen (0-indexed)
    saw_exit = False           # `message="exiting loop"` — a clean agent finish

    with open(jsonl_path, "w") as jf, open(log_path, "w") as lf:
        proc = subprocess.Popen(cmd, env=env, stdout=jf,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
        assert proc.stderr is not None         # stderr=PIPE always sets it
        deadline = t0 + timeout
        last_hb = t0
        try:
            while True:
                if time.perf_counter() > deadline:
                    proc.kill()
                    status = "timeout"
                    break
                rlist, _, _ = select.select([proc.stderr], [], [], 1.0)
                if rlist:
                    line = proc.stderr.readline()
                    if line == "":                     # stderr EOF — process done
                        break
                    lf.write(line)
                    m = _LOOP_STEP_RE.search(line)
                    if m:
                        n = int(m.group(1))
                        if n > stderr_steps:
                            stderr_steps = n
                            print(f"    · {iid} +{int(time.perf_counter()-t0)}s "
                                  f"step={n}", flush=True)
                    if "exiting loop" in line:
                        saw_exit = True
                elif proc.poll() is not None:
                    break
                now = time.perf_counter()
                if now - last_hb >= HEARTBEAT_EVERY_S:
                    print(f"    · {iid} +{int(now-t0)}s step={max(stderr_steps,0)} "
                          f"(working…)", flush=True)
                    last_hb = now
        finally:
            try:
                if proc.stderr:
                    rest = proc.stderr.read()
                    if rest:
                        lf.write(rest)
            except (OSError, ValueError):
                pass
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    wall = time.perf_counter() - t0
    metrics = parse_episode_jsonl(jsonl_path)
    if not metrics:
        # json buffer lost (timeout/crash) — synthesize from the stderr stream.
        # A timeout that never reached "exiting loop" is, per item-16's premise
        # ("long episodes are degenerate, not productive"), a degenerate loop.
        metrics = {
            "source": "stderr",
            "steps": stderr_steps + 1 if stderr_steps >= 0 else 0,
            "tool_call_rounds": None, "dropped_output": None,
            "tool_calls": None, "tool_calls_error": None, "output_tokens": None,
            "made_edit": None, "steps_to_first_edit": None,
            "first_tool_step": None, "first_tool_offset_s": None,
            "max_line_repeat": None,
            "degenerate_loop": status == "timeout" and not saw_exit,
        }
    metrics["timed_out"] = status == "timeout"
    metrics["saw_exit_loop"] = saw_exit
    if metrics.get("first_tool_offset_s") is not None and timeout > 0:
        metrics["frac_budget_to_first_tool"] = round(
            metrics["first_tool_offset_s"] / timeout, 3)
    return status, wall, metrics


def capture_model_patch(checkout: str, spec: InstanceSpec, run_dir: str) -> str:
    """Diff the checkout vs base_commit, EXCLUDING any test files the instance's
    test_patch touches (those are externally fixed). Saved to run_dir.

    Diffs the index against ``base_commit`` (not HEAD): item-16 L3 found agents
    that **commit** their fix (``git add`` + ``git commit``) — a ``--cached`` diff
    vs HEAD then shows nothing, so a real fix was mis-scored ``no-edit`` and never
    tested (e.g. sympy-12481 under NO_THINK). Staging (``add -A``) then diffing the
    index vs base_commit captures the change whether the agent committed it or left
    it in the working tree.
    """
    # Exclude (a) the instance's test files — externally fixed — and (b) the
    # harness-injected lever files (opencode.json / AGENTS.md) so they never leak
    # into the scored model patch.
    test_files = _patched_files(spec.test_patch)  # type: ignore[attr-defined]
    _git(["add", "-A"], cwd=checkout)
    excludes = [f":(exclude){p}" for p in
                (*test_files, "opencode.json", "AGENTS.md", HARNESS_PROMPT_FILE)]
    diff = _git(["diff", "--cached", spec.base_commit, "--", ".", *excludes],
                cwd=checkout)
    patch = diff.stdout
    with open(os.path.join(run_dir, "model.patch"), "w") as f:
        f.write(patch)
    return patch


def _patched_files(patch_text: str) -> list[str]:
    """Post-image (``+++ b/``) paths a patch touches, in order, deduped.

    The b-side path is the one we restore-to-base then re-apply for test files;
    ``/dev/null`` (pure deletions) is skipped.
    """
    files: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            f = line[len("+++ b/"):].strip()
            if f and f != "/dev/null" and f not in files:
                files.append(f)
    return files


def apply_test_patch(checkout: str, spec: InstanceSpec) -> bool:
    """Restore test files to base, then apply the instance's test patch."""
    for tf in _patched_files(spec.test_patch):  # type: ignore[attr-defined]
        _git(["checkout", "-f", spec.base_commit, "--", tf], cwd=checkout)
    p = subprocess.run(["git", "apply", "-v", "-"], cwd=checkout,
                       input=spec.test_patch, text=True, capture_output=True)  # type: ignore[attr-defined]
    if p.returncode != 0:
        # Fall back to a more lenient apply (whitespace, fuzz).
        p = subprocess.run(["git", "apply", "--3way", "-"], cwd=checkout,
                           input=spec.test_patch, text=True, capture_output=True)  # type: ignore[attr-defined]
    return p.returncode == 0


def _pytest_run(venv_py: str, checkout: str, spec: InstanceSpec,
                env: dict) -> str:
    """Run the instance's test FILES (the ones the test_patch touches) under the
    venv's pytest with ``-rA`` and return the combined output.

    SWE-bench FAIL_TO_PASS / PASS_TO_PASS are (often bare) test *names*, not file
    paths — so we run the whole test file(s) and match outcomes by name, the same
    convention the official harness uses (test directives = the patched files).
    """
    test_files = _patched_files(spec.test_patch)               # type: ignore[attr-defined]
    args = spec.test_cmd.split()
    if args[:2] == ["python", "-m"]:
        args = [venv_py, "-m", *args[2:]]
    cmd = args + test_files
    try:
        p = subprocess.run(cmd, cwd=checkout, env=env, capture_output=True,
                           text=True, timeout=DEFAULT_TEST_TIMEOUT)
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return "TEST-TIMEOUT"


def run_tests(checkout: str, spec: InstanceSpec, env: dict,
              run_dir: str) -> tuple[int, int, int, int, str]:
    """Run the instance's tests via the venv. Returns (f2p_ok, f2p_n, p2p_ok,
    p2p_n, log_path), counting per-test PASSED outcomes by name."""
    venv_py = os.path.join(ENVS_DIR, spec.instance_id, "bin", "python")
    if not os.path.exists(venv_py):
        return 0, len(spec.fail_to_pass), 0, len(spec.pass_to_pass), ""
    out = _pytest_run(venv_py, checkout, spec, env)
    log_path = os.path.join(run_dir, "tests.log")
    os.makedirs(run_dir, exist_ok=True)
    with open(log_path, "w") as f:
        f.write(out)
    f2p_ok = sum(1 for t in spec.fail_to_pass if _test_passed(out, t))
    p2p_ok = sum(1 for t in spec.pass_to_pass if _test_passed(out, t))
    return f2p_ok, len(spec.fail_to_pass), p2p_ok, len(spec.pass_to_pass), log_path


def _test_passed(pytest_output: str, test_name: str) -> bool:
    """True iff pytest's ``-rA`` summary reports PASSED for ``test_name``.

    Matches the final node component (after the last ``::``), parametrization
    stripped, so a bare name like ``test_decompose`` matches
    ``PASSED path/to/test_x.py::test_decompose`` without false-matching
    ``test_decompose_poly``.
    """
    target = test_name.split("::")[-1].split("[")[0].strip()
    for line in pytest_output.splitlines():
        if not line.startswith("PASSED"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        last = parts[1].split("::")[-1].split("[")[0].strip()
        if last == target:
            return True
    return False


def score_instance(spec: InstanceSpec, model_ref: str, cfg: dict, base_url: str,
                   label: str, timeout: float) -> InstanceResult:
    """Full episode: checkout -> levers -> opencode -> patch -> tests -> score.

    On a mid-episode server crash (OOM) the instance fails with reason "oom" and
    the caller restarts the server before the next instance.
    """
    run_dir = os.path.join(RUNS_DIR, label, spec.instance_id)
    checkout = clean_checkout(spec)
    env = apply_levers(checkout, cfg, model_ref, base_url)

    status, ep_wall, ep_metrics = run_opencode_episode(
        checkout, spec, model_ref, env, run_dir, timeout)

    def _result(passed, reason, f2pp=0, p2pp=0, patch_bytes=0, test_wall=0.0):
        res = InstanceResult(
            instance_id=spec.instance_id, passed=passed, reason=reason,
            episode_wall_s=round(ep_wall, 1), test_wall_s=round(test_wall, 1),
            model_patch_bytes=patch_bytes,
            fail_to_pass_passed=f2pp, fail_to_pass_total=len(spec.fail_to_pass),
            pass_to_pass_passed=p2pp, pass_to_pass_total=len(spec.pass_to_pass),
            metrics=ep_metrics, tier=instance_tier({"tier": spec.tier}, "swebench"))
        res.failure_category = classify_failure(asdict(res))  # item 17 (shared taxonomy)
        return res

    if status == "timeout":
        # Distinguish a real timeout from a server crash that stalled the call.
        if not server_healthy(base_url):
            return _result(False, "oom")
        return _result(False, "timeout")
    if not server_healthy(base_url):
        return _result(False, "oom")

    patch = capture_model_patch(checkout, spec, run_dir)
    if not patch.strip():
        return _result(False, "no-edit", patch_bytes=0)
    if not apply_test_patch(checkout, spec):
        return _result(False, "apply-failed", patch_bytes=len(patch))

    t0 = time.perf_counter()
    f2p_ok, f2p_n, p2p_ok, p2p_n, _ = run_tests(checkout, spec, env, run_dir)
    test_wall = time.perf_counter() - t0
    passed = (f2p_ok == f2p_n and f2p_n > 0 and p2p_ok == p2p_n)
    return _result(passed, "ok" if passed else "tests-failed",
                   f2pp=f2p_ok, p2pp=p2p_ok, patch_bytes=len(patch),
                   test_wall=test_wall)


# --------------------------------------------------------------------------- #
# ledger + summary (the experiment tracker)
# --------------------------------------------------------------------------- #
@dataclass
class RunRow:
    label: str
    config_name: str
    config_hash: str
    model: str
    subset_id: str
    sampling: dict
    timestamp: str
    instances: list[dict]
    passed: int
    total: int
    notes: str = ""
    # --- TODO item 14 (micro suite) — optional fields on the SHARED ledger. ---
    # SWE-bench (item-11) rows leave these at their defaults; micro (item-14) rows
    # populate them. `passed`/`total` above carry the binary count for SWE-bench
    # rows and the aggregate checks_passed/checks_total for micro rows, so both
    # suites coexist in one ledger and `write_summary` renders a table per suite.
    suite: str = "swebench"          # "swebench" | "micro"
    tiers: dict | None = None        # micro: {"1": [passed,total], "2": [...], ...}
    score: float | None = None       # micro: fractional aggregate checks_passed/checks_total
    checks_passed: int | None = None # micro: total binary checks that passed
    checks_total: int | None = None  # micro: total binary checks evaluated
    # --- item 16 K-run support: decoding is non-deterministic on MLX/Metal even at
    # temp=0 (no seed fixes it), so a lever is judged on the MEAN over K repeats and
    # a delta must clear the spread. Repeats of one config share a repeat_group. ---
    repeat_group: str = ""           # "" = standalone run; else groups the K repeats
    repeat_index: int = 0            # 1..K within the group (0 = standalone)


def append_ledger(row: RunRow) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(asdict(row)) + "\n")
    print(f"\nAppended run -> {LEDGER}")


def load_ledger() -> list[dict]:
    if not os.path.exists(LEDGER):
        return []
    rows = []
    with open(LEDGER) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _render_swebench_table(rows: list[dict]) -> list[str]:
    """SWE-bench (item-11) comparison table: config -> pass/total -> Δ vs baseline."""
    if not rows:
        return []
    baseline = next((r for r in rows if r["config_name"] == "baseline"), None)
    base_pass = baseline["passed"] if baseline else None
    lines = [
        "## SWE-bench Lite subset (item 11) — task pass/fail",
        "",
        f"Subset: `{rows[-1].get('subset_id', '?')}` · baseline pass = "
        f"{base_pass if base_pass is not None else '—'} / "
        f"{rows[-1].get('total', '?')}",
        "",
        "| config | when | sampling | score | Δ vs baseline | hash |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        delta = "—"
        if base_pass is not None:
            d = r["passed"] - base_pass
            delta = f"{d:+d}" if r["config_name"] != "baseline" else "(base)"
        samp = ", ".join(f"{k}={v}" for k, v in (r.get("sampling") or {}).items()) or "default"
        lines.append(
            f"| {r['config_name']} | {r['timestamp'][:16]} | {samp} | "
            f"{r['passed']}/{r['total']} | {delta} | `{r['config_hash']}` |")
    return lines + [""]


def _render_episode_metrics(rows: list[dict]) -> list[str]:
    """E0 episode-metrics table (item 16): the adopt/reject gradient that the
    binary pass/fail can't show. Per config: degenerate-loop rate (primary),
    timeout rate, edit rate, and mean steps / output-tokens / first-tool budget.
    """
    rows = [r for r in rows if any(i.get("metrics") for i in r.get("instances", []))]
    if not rows:
        return []
    lines = [
        "## Episode metrics (item 16 / E0) — degenerate-loop gradient",
        "",
        "Primary adopt/reject signal: **degen** (degenerate-loop rate) ↓. "
        "**dropped** = turns that spent tokens but yielded no text/tool (the "
        "tool-call-reliability floor — dominant baseline mode). `edit` = made-edit "
        "rate; `→tool` = mean fraction-of-budget to first tool call. Dashes = "
        "signal unavailable (json buffer lost on timeout).",
        "",
        "| config | when | n | degen | dropped | timeout | edit | steps | rnds | tok | →tool |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def _rate(insts, pred):
        vals = [pred(m) for i in insts if (m := i.get("metrics"))]
        vals = [v for v in vals if v is not None]
        return f"{sum(vals)/len(vals):.0%}" if vals else "—"

    def _mean(insts, key):
        vals = [m.get(key) for i in insts if (m := i.get("metrics"))]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return f"{sum(vals)/len(vals):.1f}" if vals else "—"

    for r in rows:
        insts = r.get("instances", [])
        n = sum(1 for i in insts if i.get("metrics"))
        lines.append(
            f"| {r['config_name']} | {r['timestamp'][:16]} | {n} | "
            f"{_rate(insts, lambda m: bool(m.get('degenerate_loop')))} | "
            f"{_rate(insts, lambda m: m.get('dropped_output'))} | "
            f"{_rate(insts, lambda m: bool(m.get('timed_out')))} | "
            f"{_rate(insts, lambda m: m.get('made_edit'))} | "
            f"{_mean(insts, 'steps')} | {_mean(insts, 'tool_call_rounds')} | "
            f"{_mean(insts, 'output_tokens')} | "
            f"{_mean(insts, 'frac_budget_to_first_tool')} |")
    return lines + [""]


def _render_repeat_aggregate(rows: list[dict]) -> list[str]:
    """K-run aggregate (item 16 measurement fix): for each repeat_group, the mean
    and spread (min–max) of pass-rate + key E0 rates across the K repeats. Decoding
    is non-deterministic on MLX/Metal even at temp=0 (no seed fixes it), so a lever
    is judged on the mean and a delta must clear the spread."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        g = r.get("repeat_group") or ""
        if g:
            groups.setdefault(g, []).append(r)
    groups = {g: rs for g, rs in groups.items() if len(rs) > 1}
    if not groups:
        return []

    def _row_rate(insts: list, key: str, as_bool: bool = False) -> float | None:
        vals = [m.get(key) for i in insts if (m := i.get("metrics"))]
        vals = [bool(v) if as_bool else v for v in vals if v is not None]
        return sum(1 for v in vals if v) / len(vals) if vals else None

    def _row_mean(insts: list, key: str) -> float | None:
        vals = [m.get(key) for i in insts if (m := i.get("metrics"))]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else None

    def _agg(per_row: list) -> str:
        vals = [v for v in per_row if v is not None]
        if not vals:
            return "—"
        return f"{sum(vals)/len(vals):.2f} ({min(vals):.2f}–{max(vals):.2f})"

    lines = [
        "## K-run aggregates (item 16) — mean (min–max spread) over repeats",
        "",
        "Adopt/reject on the **mean**; a lever delta must clear the **spread** "
        "(MLX/Metal decoding is non-deterministic even at temp=0 — no seed fixes it).",
        "",
        "| group | config | K | pass mean (spread) | dropped | made_edit | degen | steps |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for g, rs in groups.items():
        k = len(rs)
        total = rs[0]["total"]
        passes = [r["passed"] for r in rs]
        pass_cell = f"{sum(passes)/k:.1f}/{total} ({min(passes)}–{max(passes)})"
        dropped = _agg([_row_rate(r["instances"], "dropped_output") for r in rs])
        edit = _agg([_row_rate(r["instances"], "made_edit") for r in rs])
        degen = _agg([_row_rate(r["instances"], "degenerate_loop", True) for r in rs])
        steps = _agg([_row_mean(r["instances"], "steps") for r in rs])
        lines.append(f"| {g} | {rs[0]['config_name']} | {k} | {pass_cell} | "
                     f"{dropped} | {edit} | {degen} | {steps} |")
    return lines + [""]


def tier_breakdown(row: dict) -> dict:
    """Per-tier {pass, total, cats} for one ledger run (item 17.4).

    Uniform across suites: a tier "pass" is ``classify_failure == "ok"`` (a real
    SWE-bench flip, or all micro checks green); the histogram counts each
    instance's derived failure_category. Tier is the global ladder tier.
    """
    suite = row.get("suite", "swebench")
    out: dict[int, dict] = {}
    for inst in row.get("instances", []):
        t = instance_tier(inst, suite)
        cat = inst.get("failure_category") or classify_failure(inst)
        cell = out.setdefault(t, {"pass": 0, "total": 0, "cats": {}})
        cell["total"] += 1
        if cat == "ok":
            cell["pass"] += 1
        cats: dict[str, int] = cell["cats"]
        cats[cat] = cats.get(cat, 0) + 1
    return out


def _latest_per_config(rows: list[dict]) -> list[dict]:
    """Most-recent run per (suite, config_name), in stable suite/tier order."""
    latest: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("suite", "swebench"), r.get("config_name", "?"))
        cur = latest.get(key)
        if cur is None or r.get("timestamp", "") >= cur.get("timestamp", ""):
            latest[key] = r
    return sorted(latest.values(),
                  key=lambda r: (r.get("suite", "swebench"), r.get("config_name", "")))


def build_tier_report(rows: list[dict]) -> list[dict]:
    """Structured per-config tier report (item 17.5): the JSONL artifact + the
    source for the rendered table. One record per (suite, config) latest run with
    per-tier pass/total and the failure-mode histogram. Cheap by construction —
    pure aggregation over the ledger — so item 19 can call it as a fitness read.
    """
    base = {"swebench": "baseline", "micro": "micro-baseline"}
    # baseline per-tier pass-rate, for the delta column
    base_rate: dict[tuple, float] = {}
    for r in _latest_per_config(rows):
        suite = r.get("suite", "swebench")
        if r.get("config_name") == base.get(suite):
            for t, c in tier_breakdown(r).items():
                if c["total"]:
                    base_rate[(suite, t)] = c["pass"] / c["total"]
    report = []
    for r in _latest_per_config(rows):
        suite = r.get("suite", "swebench")
        bd = tier_breakdown(r)
        tiers = {}
        for t in sorted(bd):
            c = bd[t]
            cells: dict[str, int] = c["cats"]
            rate = c["pass"] / c["total"] if c["total"] else None
            br = base_rate.get((suite, t))
            tiers[str(t)] = {
                "pass": c["pass"], "total": c["total"],
                "pass_rate": round(rate, 3) if rate is not None else None,
                "delta_vs_baseline": (round(rate - br, 3)
                                      if rate is not None and br is not None
                                      and r.get("config_name") != base.get(suite)
                                      else None),
                "failure_histogram": dict(sorted(
                    cells.items(), key=lambda kv: (-kv[1], kv[0]))),
            }
        report.append({
            "suite": suite, "config_name": r.get("config_name"),
            "config_hash": r.get("config_hash"), "label": r.get("label"),
            "timestamp": r.get("timestamp"), "tiers": tiers,
        })
    return report


def write_tier_report(rows: list[dict]) -> list[dict]:
    """Persist the structured tier report (17.5) to TIER_REPORT (JSONL)."""
    report = build_tier_report(rows)
    os.makedirs(os.path.dirname(TIER_REPORT), exist_ok=True)
    with open(TIER_REPORT, "w") as f:
        for rec in report:
            f.write(json.dumps(rec) + "\n")
    return report


def _render_tier_report(rows: list[dict]) -> list[str]:
    """Unified 4-tier × failure-mode table (item 17.4/17.5) spanning BOTH suites.

    Per config: per-tier pass-rate (T1/T2 = synthetic micro rungs, T3/T4 = real
    SWE-bench fixes), the dominant failure modes, and Δ vs that suite's baseline.
    This is the gradient item 16 found missing — a weak model can clear T1/T2 even
    while T3/T4 sit at 0, so a lever's effect is attributable to a tier.
    """
    report = build_tier_report(rows)
    if not report:
        return []
    lines = [
        "## Tiered validation (item 17) — 4-tier ladder × failure modes",
        "",
        "One ladder over both harnesses: **T1** single tool-call · **T2** "
        "multi-step+micro-edit (synthetic) · **T3** single-file real fix · **T4** "
        "multi-file/reasoning fix (SWE-bench). Cells = pass/total; Δ vs the suite "
        "baseline. `modes` = derived failure_category histogram (item-16 taxonomy).",
        "",
        "| config | suite | T1 | T2 | T3 | T4 | Δ | top failure modes |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def _cell(rec_tiers: dict, t: int) -> str:
        c = rec_tiers.get(str(t))
        if not c or not c["total"]:
            return "—"
        return f"{c['pass']}/{c['total']}"

    for rec in report:
        tcells = [_cell(rec["tiers"], t) for t in (1, 2, 3, 4)]
        deltas = [c["delta_vs_baseline"] for c in rec["tiers"].values()
                  if c.get("delta_vs_baseline") is not None]
        delta = f"{sum(deltas)/len(deltas):+.2f}" if deltas else "—"
        hist: dict[str, int] = {}
        for c in rec["tiers"].values():
            for k, v in c["failure_histogram"].items():
                if k != "ok":
                    hist[k] = hist.get(k, 0) + v
        modes = " ".join(f"{k}×{v}" for k, v in
                         sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:4]) or "—"
        lines.append(
            f"| {rec['config_name']} | {rec['suite']} | {tcells[0]} | {tcells[1]} "
            f"| {tcells[2]} | {tcells[3]} | {delta} | {modes} |")
    return lines + ["",
                    f"Structured per-tier report (item 17.5) → `{TIER_REPORT}`", ""]


def _render_micro_table(rows: list[dict]) -> list[str]:
    """Micro-suite (item-14) table: config -> per-tier + fractional aggregate -> Δ.

    The headline score is the fractional aggregate (checks passed / checks total);
    the Δ is against the ``micro-baseline`` config. Per-tier columns show each
    tier's binary-check pass-rate so a lever's effect is attributable to a tier.
    """
    if not rows:
        return []
    # Most recent micro-baseline row is the bar (the experiment evolves; a later
    # re-baseline supersedes an earlier one).
    base = next((r for r in reversed(rows)
                 if r["config_name"] == "micro-baseline"), None)
    base_score = base.get("score") if base else None

    def _tier(r: dict, t: str) -> str:
        tiers = r.get("tiers") or {}
        cell = tiers.get(t)
        return f"{cell[0]}/{cell[1]}" if cell else "—"

    def _pct(r: dict) -> str:
        s = r.get("score")
        cp, ct = r.get("checks_passed"), r.get("checks_total")
        if s is None:
            return "—"
        return f"{s:.2f} ({cp}/{ct})"

    lines = [
        "## Synthetic micro-suite (item 14) — tool-call fidelity gradient",
        "",
        f"Suite: `{rows[-1].get('subset_id', '?')}` · baseline score = "
        f"{base_score if base_score is not None else '—'}",
        "",
        "| config | when | tier1 | tier2 | tier3 | score (passed/total) | Δ vs baseline | hash |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        delta = "—"
        if base_score is not None and r.get("score") is not None:
            d = r["score"] - base_score
            delta = "(base)" if r["config_name"] == "micro-baseline" else f"{d:+.2f}"
        lines.append(
            f"| {r['config_name']} | {r['timestamp'][:16]} | {_tier(r, '1')} | "
            f"{_tier(r, '2')} | {_tier(r, '3')} | {_pct(r)} | {delta} | "
            f"`{r['config_hash']}` |")
    return lines + [""]


def write_summary() -> str:
    rows = load_ledger()
    lines = ["# Harness-engineering experiment ledger (TODO items 11 + 14 + 16 + 17)", ""]
    if not rows:
        lines.append("_No runs recorded yet._")
        out = "\n".join(lines) + "\n"
        os.makedirs(os.path.dirname(SUMMARY_MD), exist_ok=True)
        with open(SUMMARY_MD, "w") as f:
            f.write(out)
        return out
    swebench = [r for r in rows if r.get("suite", "swebench") != "micro"]
    micro = [r for r in rows if r.get("suite") == "micro"]
    lines += _render_tier_report(rows)          # item 17 — unified, both suites
    write_tier_report(rows)                      # item 17.5 — structured JSONL artifact
    lines += _render_swebench_table(swebench)
    lines += _render_episode_metrics(swebench)
    lines += _render_repeat_aggregate(swebench)
    lines += _render_micro_table(micro)
    lines += ["Per-instance / per-test detail is in the JSONL ledger "
              f"(`{LEDGER}`); per-run artifacts under `{RUNS_DIR}`."]
    out = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(SUMMARY_MD), exist_ok=True)
    with open(SUMMARY_MD, "w") as f:
        f.write(out)
    return out


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def _hydrate(spec: InstanceSpec) -> InstanceSpec:
    """Attach the cached SWE row's problem_statement + test_patch onto a spec."""
    row = fetch_instance(spec.instance_id)
    spec.problem = row.get("problem_statement", "")          # type: ignore[attr-defined]
    spec.test_patch = row.get("test_patch", "")              # type: ignore[attr-defined]
    spec.gold_patch = row.get("patch", "")                   # type: ignore[attr-defined]
    return spec


DEFAULT_TEST_CMD = "python -m pytest -rA -p no:cacheprovider --no-header -q"


def _clone_repo(repo: str) -> str:
    """Clone github.com/<repo> into repos/<slug> (cached). Online on first call."""
    repo_dir = os.path.join(REPOS_DIR, _slug(repo))
    if os.path.isdir(os.path.join(repo_dir, ".git")):
        return repo_dir
    os.makedirs(REPOS_DIR, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    print(f"  cloning {url} …", flush=True)
    cl = _run(["git", "clone", url, repo_dir], timeout=1200)
    if cl.returncode != 0:
        raise RuntimeError(f"clone failed: {cl.stderr.strip()[:300]}")
    return repo_dir


def _provision_env(spec: InstanceSpec, repo_dir: str, force: bool,
                   python: str | None = None) -> tuple[bool, str]:
    """Create a uv venv for the instance and install the repo + pytest, editable.

    Best-effort across the common SWE-bench install shapes (PEP 621 extras,
    requirements files, bare ``-e .``). Returns (ok, note). Native, no Docker —
    so an instance whose deps don't build on macOS arm64 simply doesn't make the
    cut (recorded, dropped), which is the documented curation screen. ``python``
    pins the interpreter (SWE-bench Lite targets 3.9-3.11; uv auto-downloads it).
    """
    env_dir = os.path.join(ENVS_DIR, spec.instance_id)
    py = os.path.join(env_dir, "bin", "python")
    if os.path.exists(py) and not force:
        return True, "env cached"
    if os.path.isdir(env_dir):
        shutil.rmtree(env_dir)
    venv = _run(["uv", "venv", *(["--python", python] if python else []), env_dir],
                timeout=600)
    if venv.returncode != 0:
        return False, f"uv venv failed: {venv.stderr.strip()[:200]}"
    # Pin the repo at base_commit before installing so deps match the instance.
    _git(["checkout", "-f", spec.base_commit], cwd=repo_dir)
    notes = []
    for spec_extra in (".[test]", ".[tests]", ".[dev]", "."):
        inst = _run(["uv", "pip", "install", "--python", py, "-e", spec_extra],
                    cwd=repo_dir, timeout=1800)
        if inst.returncode == 0:
            notes.append(f"installed {spec_extra}")
            break
    else:
        return False, f"editable install failed: {inst.stderr.strip()[:200]}"
    # Ensure a test runner is present.
    _run(["uv", "pip", "install", "--python", py, "pytest"], cwd=repo_dir, timeout=600)
    return True, "; ".join(notes)


def _verify_with_gold(spec: InstanceSpec, repo_dir: str) -> tuple[bool, str]:
    """Apply gold+test patches and confirm the FULL scoring predicate holds:
    every FAIL_TO_PASS flips to passing AND every PASS_TO_PASS still passes.

    This proves the env faithfully runs the instance's tests AND that the
    instance is *winnable* in this env — the gate for entering the frozen subset
    (an instance whose P2P can't pass natively could never be scored a pass).
    """
    py = os.path.join(ENVS_DIR, spec.instance_id, "bin", "python")
    _git(["clean", "-xffd"], cwd=repo_dir)
    _git(["checkout", "-f", spec.base_commit], cwd=repo_dir)
    if spec.gold_patch.strip():  # type: ignore[attr-defined]
        g = subprocess.run(["git", "apply", "--3way", "-"], cwd=repo_dir,
                           input=spec.gold_patch, text=True, capture_output=True)  # type: ignore[attr-defined]
        if g.returncode != 0:
            return False, "gold patch did not apply"
    if not apply_test_patch(repo_dir, spec):
        return False, "test patch did not apply"
    out = _pytest_run(py, repo_dir, spec, dict(os.environ))
    if out.strip() == "TEST-TIMEOUT":
        return False, "gold verify timed out"
    f2p_ok = sum(1 for t in spec.fail_to_pass if _test_passed(out, t))
    p2p_ok = sum(1 for t in spec.pass_to_pass if _test_passed(out, t))
    f2p_n, p2p_n = len(spec.fail_to_pass), len(spec.pass_to_pass)
    if f2p_ok == f2p_n and f2p_ok > 0 and p2p_ok == p2p_n:
        return True, f"gold flips {f2p_ok}/{f2p_n} F2P, {p2p_ok}/{p2p_n} P2P pass"
    return False, f"gold flips {f2p_ok}/{f2p_n} F2P, {p2p_ok}/{p2p_n} P2P"


def cmd_prepare(args: argparse.Namespace) -> int:
    """Curate + freeze the subset: fetch, context-screen, provision, verify."""
    existing = {s.instance_id: s for s in load_subset()}
    # No --instances given → re-prepare exactly the frozen subset (the manifest is
    # the single source of truth, so a clean-machine rerun needs no id list).
    instances = args.instances or [s.instance_id for s in existing.values()]
    if not instances:
        print(f"error: no --instances given and no frozen subset at "
              f"{SUBSET_MANIFEST} to re-prepare", file=sys.stderr)
        return 2
    for iid in instances:
        print(f"\n=== preparing {iid} ===", flush=True)
        try:
            row = fetch_instance(iid)
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP — fetch failed: {e}", file=sys.stderr)
            continue
        problem = row.get("problem_statement", "")
        est = _est_tokens(problem) + 8000  # + a repo-context budget headroom
        if est > CONTEXT_SCREEN_TOKENS:
            print(f"  SKIP — est context {est} > screen {CONTEXT_SCREEN_TOKENS}")
            continue
        def _as_list(v: object) -> list[str]:
            return json.loads(v) if isinstance(v, str) else v  # type: ignore[return-value]
        spec = InstanceSpec(
            instance_id=iid, repo=row["repo"], base_commit=row["base_commit"],
            test_cmd=DEFAULT_TEST_CMD, est_context_tokens=est,
            fail_to_pass=_as_list(row["FAIL_TO_PASS"]),
            pass_to_pass=_as_list(row["PASS_TO_PASS"]))
        spec.problem = problem                       # type: ignore[attr-defined]
        spec.test_patch = row.get("test_patch", "")  # type: ignore[attr-defined]
        spec.gold_patch = row.get("patch", "")       # type: ignore[attr-defined]
        try:
            repo_dir = _clone_repo(spec.repo)
            ok, note = _provision_env(spec, repo_dir, args.force, args.python)
            if not ok:
                print(f"  DROP — {note}")
                continue
            vok, vnote = _verify_with_gold(spec, repo_dir)
            spec.prepared = vok
            spec.notes = f"{note}; {vnote}"
            print(f"  {'KEEP' if vok else 'DROP'} — {vnote}")
            if vok:
                existing[iid] = spec
        except Exception as e:  # noqa: BLE001
            print(f"  DROP — error: {e}", file=sys.stderr)
            continue
    save_subset(list(existing.values()))
    kept = sum(1 for s in existing.values() if s.prepared)
    print(f"\nfrozen subset now holds {kept} prepared instance(s)")
    return 0


_TEST_PATH_RE = re.compile(r"(^|/)(tests?|testing)(/|$)|(^|/)(conftest|test_[^/]*|[^/]*_test)\.py$")


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def assign_tier(spec: InstanceSpec, gold_patch: str) -> None:
    """Bucket one instance into the global ladder (item 17.3), in place.

    Offline + reproducible from the cached gold patch + the F2P set:
      * n_files     = non-test source files the gold patch edits
      * hunks       = number of ``@@`` change blocks (edit sites)
      * needs_search= the fix spans >1 file OR >1 edit site (must locate each)
      * tier 3      = the easiest real fixes: ONE file, ONE hunk, ONE F2P test
      * tier 4      = anything multi- (file / hunk / F2P) → more reasoning
    """
    src = [f for f in _patched_files(gold_patch) if not _is_test_path(f)]
    hunks = gold_patch.count("@@ -")
    f2p = len(spec.fail_to_pass)
    spec.n_files = len(src)
    spec.needs_search = len(src) > 1 or hunks > 1
    spec.needs_bash = False
    multi = len(src) > 1 or hunks > 1 or f2p > 1
    spec.tier = 4 if multi else 3
    spec.expected_tool_seq = (["grep", "read", "edit"] if spec.needs_search
                              else ["read", "edit"])


def cmd_tier(args: argparse.Namespace) -> int:
    """(offline) Assign the item-17 difficulty tier + metadata to every subset
    instance from its cached gold patch, and write it back into the manifest."""
    subset = load_subset()
    if not subset:
        print(f"error: no frozen subset at {SUBSET_MANIFEST} — run `prepare` first",
              file=sys.stderr)
        return 2
    # Preserve the original freeze timestamp — the subset is unchanged, only its
    # tier metadata is (re)derived.
    frozen_at = None
    try:
        with open(SUBSET_MANIFEST) as f:
            frozen_at = json.load(f).get("frozen_at")
    except (OSError, ValueError):
        pass
    print(f"{'instance':26s} {'tier':>4}  {'files':>5} {'hunks':>5} {'F2P':>3}  search")
    for spec in subset:
        row = fetch_instance(spec.instance_id)        # cached → offline
        assign_tier(spec, row.get("patch", ""))
        hunks = row.get("patch", "").count("@@ -")
        print(f"{spec.instance_id:26s} T{spec.tier:<3}  {spec.n_files:>5} "
              f"{hunks:>5} {len(spec.fail_to_pass):>3}  {spec.needs_search}")
    save_subset(subset, {"frozen_at": frozen_at} if frozen_at else None)
    counts: dict[int, int] = {}
    for s in subset:
        counts[s.tier] = counts.get(s.tier, 0) + 1
    print("\ntier histogram: " +
          "  ".join(f"T{t}={counts[t]}" for t in sorted(counts)) +
          f"   (T1/T2 live in the synthetic micro-suite — {GLOBAL_TIERS[1]})")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Render + persist the item-17 tiered report (also done by `summary`)."""
    write_summary()
    rows = load_ledger()
    report = build_tier_report(rows)
    if not report:
        print("_No runs in the ledger yet._")
        return 0
    print("\n".join(_render_tier_report(rows)))
    print(f"Structured report ({len(report)} configs) -> {TIER_REPORT}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    subset = load_subset()
    if not subset:
        print(f"error: no frozen subset at {SUBSET_MANIFEST} — run `prepare` first",
              file=sys.stderr)
        return 2
    if args.instances:
        subset = [s for s in subset if s.instance_id in set(args.instances)]
        if not subset:
            print("error: none of the requested instances are in the subset",
                  file=sys.stderr)
            return 2
    not_ready = [s.instance_id for s in subset if not s.prepared]
    if not_ready:
        print(f"error: these instances are not prepared: {not_ready}\n"
              f"       run `prepare` (online) before scoring.", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    if not server_healthy(args.base_url):
        # Self-heal: a prior run may have left the server OOM-dead. Try once to
        # bring it back before giving up (so a multi-config sweep doesn't abort
        # just because the last instance of the previous config crashed it).
        print(f"MLX endpoint {args.base_url} is down — attempting restart …")
        if not restart_server(args.base_url):
            print(f"error: MLX endpoint {args.base_url} is down and restart "
                  f"failed — `make mlx-up` first", file=sys.stderr)
            return 2
    served = detect_model(args.base_url)
    model_ref = args.model or f"{DEFAULT_PROVIDER}/{served}"
    base_label = args.label or f"{cfg['name']}-{time.strftime('%Y%m%d-%H%M')}"
    repeats = max(1, int(getattr(args, "repeats", 1) or 1))
    group = base_label if repeats > 1 else ""

    print(f"Scoring config '{cfg['name']}' (hash {config_hash(cfg)})  "
          f"model={model_ref}  subset={len(subset)} instances"
          f"{f'  repeats={repeats}' if repeats > 1 else ''}\n")
    pass_counts: list[int] = []
    for rep in range(1, repeats + 1):
        label = base_label if repeats == 1 else f"{base_label}-r{rep}"
        if repeats > 1:
            print(f"\n=== repeat {rep}/{repeats}  (label {label}) ===", flush=True)
        results = _score_subset(subset, model_ref, cfg, args.base_url, label,
                                args.timeout)
        passed = sum(1 for r in results if r.passed)
        pass_counts.append(passed)
        append_ledger(RunRow(
            label=label, config_name=cfg["name"], config_hash=config_hash(cfg),
            model=model_ref, subset_id=_subset_id(subset),
            sampling=cfg.get("sampling") or {}, timestamp=_now_iso(),
            instances=[asdict(r) for r in results], passed=passed,
            total=len(results), notes=cfg.get("description", ""),
            repeat_group=group, repeat_index=rep if repeats > 1 else 0))
        print(f"\nconfig '{cfg['name']}'"
              f"{f' repeat {rep}/{repeats}' if repeats > 1 else ''}: "
              f"{passed}/{len(results)} passed")

    if repeats > 1:
        mean = sum(pass_counts) / len(pass_counts)
        print(f"\n=== {repeats}-run aggregate for '{cfg['name']}': pass mean "
              f"{mean:.1f}/{len(subset)} (spread {min(pass_counts)}–"
              f"{max(pass_counts)} over {pass_counts}) — a lever delta must clear "
              f"this spread (MLX/Metal nondeterminism; no seed fixes it) ===")
    write_summary()
    print(f"Summary table -> {SUMMARY_MD}")
    return 0


def _score_subset(subset: list[InstanceSpec], model_ref: str, cfg: dict,
                  base_url: str, label: str,
                  timeout: float) -> list[InstanceResult]:
    """One full pass over the subset (used once per K-run repeat). Restarts the
    server after any OOM so the next instance/repeat starts healthy."""
    results: list[InstanceResult] = []
    for i, spec in enumerate(subset, 1):
        _hydrate(spec)
        print(f"[{i}/{len(subset)}] {spec.instance_id} …", flush=True)
        try:
            r = score_instance(spec, model_ref, cfg, base_url, label, timeout)
        except Exception as e:  # noqa: BLE001 — one bad instance must not abort the run
            print(f"  error: {e}", file=sys.stderr)
            r = InstanceResult(spec.instance_id, False, f"error:{e}", 0, 0, 0,
                               0, len(spec.fail_to_pass), 0, len(spec.pass_to_pass))
        results.append(r)
        mark = "PASS" if r.passed else "FAIL"
        print(f"  -> {mark} ({r.reason})  episode={r.episode_wall_s}s  "
              f"F2P={r.fail_to_pass_passed}/{r.fail_to_pass_total}", flush=True)
        if r.reason == "oom":
            restart_server(base_url)
    return results


def _subset_id(subset: list[InstanceSpec]) -> str:
    ids = ",".join(sorted(s.instance_id for s in subset))
    return hashlib.sha256(ids.encode()).hexdigest()[:12]


def cmd_summary(args: argparse.Namespace) -> int:
    print(write_summary())
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Offline sanity checks for the scoring machinery (no model needed)."""
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    # 1. patched-file extraction from a unified diff
    sample = ("--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@\n-x\n+y\n"
              "--- a/tests/test_mod.py\n+++ b/tests/test_mod.py\n@@\n+def test(): pass\n")
    files = _patched_files(sample)
    check("patched-files parses both paths",
          files == ["pkg/mod.py", "tests/test_mod.py"])

    # 2. pytest PASSED parsing
    out = "PASSED tests/test_mod.py::test_alpha\nFAILED tests/test_mod.py::test_beta\n"
    check("_test_passed true for PASSED node",
          _test_passed(out, "tests/test_mod.py::test_alpha"))
    check("_test_passed false for FAILED node",
          not _test_passed(out, "tests/test_mod.py::test_beta"))

    # 3. deep-merge of opencode config fragments
    merged = _deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}})
    check("deep-merge overrides nested key only", merged == {"a": {"b": 1, "c": 3}})

    # 4. config hash is stable + ignores description
    h1 = config_hash({"name": "x", "sampling": {"temperature": 0.0}, "description": "a"})
    h2 = config_hash({"name": "x", "sampling": {"temperature": 0.0}, "description": "b"})
    check("config hash ignores description", h1 == h2)

    # 5. summary renders with an empty ledger without crashing
    check("summary renders", "items 11 + 14" in write_summary())

    # 6. mlx.sh present (restart path target)
    check("scripts/mlx.sh exists", os.path.exists(MLX_SH))

    # 7. sampling forwarding (item 16 / E-sampling): the whole `sampling` block —
    #    including non-OpenAI keys like `repetition_penalty` (L1) — must land
    #    verbatim under the served model's `options` in the written opencode.json.
    if getattr(args, "check_sampling", False):
        import tempfile
        samp = {"temperature": 0.0, "top_p": 0.9,
                "repetition_penalty": 1.3, "repetition_context_size": 64}
        with tempfile.TemporaryDirectory() as td:
            apply_levers(td, {"name": "s", "sampling": samp},
                         f"{DEFAULT_PROVIDER}/probe-model", DEFAULT_BASE_URL)
            with open(os.path.join(td, "opencode.json")) as f:
                written = json.load(f)
        opts = (written.get("provider", {}).get(DEFAULT_PROVIDER, {})
                .get("models", {}).get("probe-model", {}).get("options", {}))
        check("sampling block forwarded verbatim into model options",
              all(opts.get(k) == v for k, v in samp.items()))
        check("anti-repetition param (repetition_penalty) forwarded",
              opts.get("repetition_penalty") == 1.3)

    # 8. E0 metrics parser (item 16) on a synthetic NDJSON stream matching the
    #    verified `opencode run --format json` schema: 3 steps, a read then an
    #    edit (so steps_to_first_edit=2, made_edit), plus repeated planning text.
    import tempfile
    plan = "I will analyze the failing test and locate the root cause."
    events = [
        {"type": "step_start", "timestamp": 1000, "part": {"type": "step-start"}},
        {"type": "text", "timestamp": 1001,
         "part": {"type": "text", "text": "\n".join([plan] * 7)}},
        {"type": "tool_use", "timestamp": 1500,
         "part": {"type": "tool", "tool": "read", "callID": "c1",
                  "state": {"status": "completed", "input": {"filePath": "m.py"}}}},
        {"type": "step_finish", "timestamp": 1600,
         "part": {"reason": "tool-calls", "tokens": {"output": 40}}},
        {"type": "step_start", "timestamp": 1700, "part": {"type": "step-start"}},
        {"type": "tool_use", "timestamp": 1800,
         "part": {"type": "tool", "tool": "edit", "callID": "c2",
                  "state": {"status": "completed", "input": {"filePath": "m.py"}}}},
        {"type": "step_finish", "timestamp": 1900,
         "part": {"reason": "tool-calls", "tokens": {"output": 60}}},
        {"type": "step_start", "timestamp": 2000, "part": {"type": "step-start"}},
        {"type": "step_finish", "timestamp": 2100,
         "part": {"reason": "stop", "tokens": {"output": 10}}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
        tf.write("\n".join(json.dumps(e) for e in events) + "\n")
        ndjson_path = tf.name
    em = parse_episode_jsonl(ndjson_path)
    os.unlink(ndjson_path)
    check("E0 parse: counts steps/tools/tokens/rounds",
          em.get("steps") == 3 and em.get("tool_calls") == 2
          and em.get("output_tokens") == 110 and em.get("tool_call_rounds") == 2)
    check("E0 parse: first-edit + made_edit",
          em.get("made_edit") is True and em.get("steps_to_first_edit") == 2)
    check("E0 parse: degenerate-loop detected (7x repeated plan line)",
          em.get("degenerate_loop") is True and em.get("max_line_repeat") == 7)
    check("E0 parse: first-tool offset (0.5s)",
          em.get("first_tool_offset_s") == 0.5)
    check("E0 parse: dropped_output False when tools/text present",
          em.get("dropped_output") is False)
    check("E0 parse: empty/missing file → {} (timeout fallback path)",
          parse_episode_jsonl("/no/such/file.jsonl") == {})

    # 8b. dropped-output mode: output tokens spent, but NO text + NO tool activity
    #     (the dominant baseline failure — malformed call dropped, loop stops).
    dropped = [
        {"type": "step_start", "timestamp": 1, "part": {"type": "step-start"}},
        {"type": "step_finish", "timestamp": 2,
         "part": {"reason": "stop", "tokens": {"output": 142}}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
        tf.write("\n".join(json.dumps(e) for e in dropped) + "\n")
        dpath = tf.name
    dm = parse_episode_jsonl(dpath)
    os.unlink(dpath)
    check("E0 parse: dropped_output True (142 tok, no text, no tool)",
          dm.get("dropped_output") is True and dm.get("tool_call_rounds") == 0)

    # 9. K-run aggregate (item 16 measurement fix): mean + spread over a
    #    repeat_group. Two repeats with dropped-rate 1/2 and 2/2 → mean 0.75,
    #    spread 0.50–1.00 (the spread a lever delta must clear).
    def _mk(drop_flags: list) -> dict:
        insts = [{"metrics": {"dropped_output": d}, "passed": False} for d in drop_flags]
        return {"config_name": "k", "repeat_group": "grp", "passed": 0,
                "total": len(insts), "instances": insts}
    agg = _render_repeat_aggregate([_mk([True, False]), _mk([True, True])])
    agg_txt = "\n".join(agg)
    check("K-run aggregate groups repeats + shows dropped mean (0.75) & spread",
          "0.75 (0.50–1.00)" in agg_txt)
    check("K-run aggregate ignores singleton groups",
          _render_repeat_aggregate([_mk([True, False])]) == [])

    # 10. patch capture vs base_commit (item 16 L3): an agent that COMMITS its fix
    #     (git add + commit) must STILL be captured — a `--cached` diff vs HEAD
    #     would show nothing and mis-score it `no-edit` (the sympy-12481 defect).
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as repo:
        for a in (["init", "-q"], ["config", "user.email", "t@t.t"],
                  ["config", "user.name", "t"]):
            _git(a, cwd=repo)
        with open(os.path.join(repo, "m.py"), "w") as f:
            f.write("x = 1\n")
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-qm", "base"], cwd=repo)
        base = _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
        with open(os.path.join(repo, "m.py"), "w") as f:
            f.write("x = 2\n")
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-qm", "agent fix"], cwd=repo)   # the model COMMITS its fix
        spec = SimpleNamespace(base_commit=base, test_patch="")
        with tempfile.TemporaryDirectory() as rd:
            patch = capture_model_patch(repo, spec, rd)  # type: ignore[arg-type]
        check("patch capture finds a COMMITTED fix (L3 / diff vs base_commit)",
              "+x = 2" in patch and "m.py" in patch)

    # 11. item 17 — shared failure_category classifier (decision B: derived). One
    #     SWE-bench instance per branch, exercising the precedence order.
    def _sw(passed=False, reason="", metrics=None, p2pp=0, p2pt=0):
        return {"reason": reason, "passed": passed, "metrics": metrics or {},
                "pass_to_pass_passed": p2pp, "pass_to_pass_total": p2pt}
    check("classify: pass → ok", classify_failure(_sw(passed=True)) == "ok")
    check("classify: oom", classify_failure(_sw(reason="oom")) == "oom")
    check("classify: degenerate-loop wins over timeout",
          classify_failure(_sw(reason="timeout",
                               metrics={"degenerate_loop": True})) == "degenerate-loop")
    check("classify: plain timeout",
          classify_failure(_sw(reason="timeout")) == "timeout")
    check("classify: no-edit", classify_failure(_sw(reason="no-edit")) == "no-edit")
    check("classify: apply-failed → edit-mismatch",
          classify_failure(_sw(reason="apply-failed")) == "edit-mismatch")
    check("classify: errored grep w/o edit → grep-parse-error",
          classify_failure(_sw(reason="tests-failed",
                               metrics={"errored_tools": ["grep"], "made_edit": False},
                               p2pp=1, p2pt=1)) == "grep-parse-error")
    check("classify: tests-failed + P2P regression → catastrophic-edit",
          classify_failure(_sw(reason="tests-failed", p2pp=2, p2pt=3)) == "catastrophic-edit")
    check("classify: tests-failed, P2P intact → tests-failed (wrong fix)",
          classify_failure(_sw(reason="tests-failed", p2pp=3, p2pt=3)) == "tests-failed")
    # micro dispatch (row carries `status`+`checks_total`, no `reason`)
    check("classify: micro all-checks-green → ok",
          classify_failure({"status": "ok", "tier": 1,
                            "checks_passed": 3, "checks_total": 3}) == "ok")
    check("classify: micro edit-tier miss → edit-mismatch",
          classify_failure({"status": "ok", "tier": 3,
                            "checks_passed": 1, "checks_total": 4}) == "edit-mismatch")
    check("classify: micro call-tier miss → no-edit",
          classify_failure({"status": "ok", "tier": 1,
                            "checks_passed": 1, "checks_total": 3}) == "no-edit")

    # 12. item 17 — global tier mapping. micro local 1/2/3 → T1/T2/T2; SWE-bench
    #     rows carry their assigned tier; un-bucketed SWE-bench defaults to T3.
    check("instance_tier: micro 1→T1, 2→T2, 3→T2",
          [instance_tier({"tier": t}, "micro") for t in (1, 2, 3)] == [1, 2, 2])
    check("instance_tier: swebench reads row tier; default 3",
          instance_tier({"tier": 4}, "swebench") == 4
          and instance_tier({}, "swebench") == 3)

    # 13. item 17 — assign_tier buckets a single-file/single-hunk/single-F2P fix as
    #     T3 and a multi-hunk one as T4 (the 17.3 rule, offline from a gold patch).
    easy = SimpleNamespace(fail_to_pass=["t"], n_files=0, needs_search=False,
                           needs_bash=False, tier=0, expected_tool_seq=[])
    assign_tier(easy, "--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -1 +1 @@\n-a\n+b\n")  # type: ignore[arg-type]
    check("assign_tier: 1 file / 1 hunk / 1 F2P → T3 (read,edit)",
          easy.tier == 3 and easy.n_files == 1 and not easy.needs_search
          and easy.expected_tool_seq == ["read", "edit"])
    hard = SimpleNamespace(fail_to_pass=["t1", "t2"], n_files=0, needs_search=False,
                           needs_bash=False, tier=0, expected_tool_seq=[])
    hard_patch = ("--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -1 +1 @@\n-a\n+b\n"
                  "--- a/tests/test_m.py\n+++ b/tests/test_m.py\n@@ -1 +1 @@\n-x\n+y\n")
    assign_tier(hard, hard_patch)  # type: ignore[arg-type]
    check("assign_tier: test file excluded from n_files; multi-F2P → T4",
          hard.tier == 4 and hard.n_files == 1)

    # 14. item 17 — errored_tools captured by the E0 parser (grep/edit distinction).
    errev = [
        {"type": "step_start", "timestamp": 1, "part": {"type": "step-start"}},
        {"type": "tool_use", "timestamp": 2,
         "part": {"type": "tool", "tool": "grep", "callID": "e1",
                  "state": {"status": "error", "input": {"pattern": "["}}}},
        {"type": "step_finish", "timestamp": 3,
         "part": {"reason": "tool-calls", "tokens": {"output": 20}}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
        tf.write("\n".join(json.dumps(e) for e in errev) + "\n")
        epath = tf.name
    pe = parse_episode_jsonl(epath)
    os.unlink(epath)
    check("E0 parse: errored_tools records the failing grep call",
          pe.get("errored_tools") == ["grep"] and pe.get("tool_calls_error") == 1)

    # 15. item 17 — tier report: T3 row (one pass, one wrong-fix) + a micro T1 row
    #     render into the unified table with per-tier cells + a failure histogram.
    sw_rows = [{
        "suite": "swebench", "config_name": "baseline", "timestamp": "2026-01-01T00:00",
        "config_hash": "h", "label": "l", "passed": 1, "total": 2,
        "instances": [
            {"reason": "ok", "passed": True, "tier": 3, "metrics": {},
             "pass_to_pass_passed": 1, "pass_to_pass_total": 1},
            {"reason": "tests-failed", "passed": False, "tier": 3, "metrics": {},
             "pass_to_pass_passed": 1, "pass_to_pass_total": 1}],
    }, {
        "suite": "micro", "config_name": "micro-baseline", "timestamp": "2026-01-01T00:00",
        "config_hash": "h", "label": "m", "passed": 3, "total": 3,
        "instances": [{"status": "ok", "tier": 1, "checks_passed": 3, "checks_total": 3}],
    }]
    rep = build_tier_report(sw_rows)
    sw_rec = next(r for r in rep if r["suite"] == "swebench")
    check("tier report: T3 breakdown = 1/2 pass with tests-failed in histogram",
          sw_rec["tiers"]["3"]["pass"] == 1 and sw_rec["tiers"]["3"]["total"] == 2
          and sw_rec["tiers"]["3"]["failure_histogram"].get("tests-failed") == 1)
    micro_rec = next(r for r in rep if r["suite"] == "micro")
    check("tier report: micro T1 = 1/1 pass (local tier 1 → ladder T1)",
          micro_rec["tiers"]["1"]["pass"] == 1)
    rtxt = "\n".join(_render_tier_report(sw_rows))
    check("tier report renders the 4-tier header + a config row",
          "4-tier ladder" in rtxt and "| baseline | swebench |" in rtxt)

    print(f"\nselftest: {'OK' if ok else 'FAILURES'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("prepare", help="(online, one-time) curate+freeze the subset")
    pr.add_argument("--instances", nargs="+", default=None,
                    help="SWE-bench Lite instance ids to curate "
                         "(default: re-prepare the frozen subset in the manifest)")
    pr.add_argument("--force", action="store_true", help="re-provision even if cached")
    pr.add_argument("--python", default=None,
                    help="pin the venv interpreter (e.g. 3.11; SWE-bench Lite targets 3.9-3.11)")
    pr.set_defaults(func=cmd_prepare)

    rn = sub.add_parser("run", help="score the frozen subset with a lever config")
    rn.add_argument("--config", required=True,
                    help="lever config name (scripts/harness_configs/<name>.json)")
    rn.add_argument("--instances", nargs="+", help="limit to these instance ids")
    rn.add_argument("--repeats", type=int, default=1,
                    help="run the subset K times (one ledger row per repeat, sharing "
                         "a repeat_group); adopt/reject on the K-run MEAN, since "
                         "MLX/Metal decoding is non-deterministic (no seed fixes it)")
    rn.add_argument("--label", default=None,
                    help="run label (default: config+timestamp); repeats append -rN")
    rn.add_argument("--base-url", default=DEFAULT_BASE_URL)
    rn.add_argument("--model", default=None,
                    help="opencode model ref (default: mlx-local/<detected>)")
    rn.add_argument("--timeout", type=float, default=DEFAULT_INSTANCE_TIMEOUT,
                    help="hard per-instance wall-clock cap (s)")
    rn.set_defaults(func=cmd_run)

    tr = sub.add_parser("tier", help="(offline) assign item-17 difficulty tiers + "
                                     "metadata to the subset from cached gold patches")
    tr.set_defaults(func=cmd_tier)

    sm = sub.add_parser("summary", help="regenerate + print the markdown ledger table")
    sm.set_defaults(func=cmd_summary)

    rp = sub.add_parser("report", help="render + persist the item-17 tiered "
                                       "validation report (per-tier × failure-mode)")
    rp.set_defaults(func=cmd_report)

    st = sub.add_parser("selftest", help="offline sanity checks (no model needed)")
    st.add_argument("--check-sampling", action="store_true",
                    help="also assert the sampling block (incl. repetition_penalty) "
                         "is forwarded into the written opencode.json model options")
    st.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
