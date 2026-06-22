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
    wall-clock cap (default 30 min). On timeout OR a detected MLX-server crash
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
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

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

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_PROVIDER = "mlx-local"          # opencode provider id (docs/opencode-config.md)
HARNESS_PROMPT_FILE = ".harness_prompt.md"  # L3 prompt-replacement file (per-checkout)
DEFAULT_INSTANCE_TIMEOUT = 30 * 60      # hard per-instance wall-clock cap (s)
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
    the OpenAI request; temperature/top_p/top_k are placed there. This path is
    version-sensitive (docs/opencode-config.md) — `selftest --check-sampling`
    asserts the values actually reach the endpoint before a lever run is trusted.
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


def run_opencode_episode(checkout: str, spec: InstanceSpec, model_ref: str,
                         env: dict, run_dir: str, timeout: float) -> tuple[str, float]:
    """Drive opencode headlessly in the checkout. Returns (status, wall_s).

    status is "ok" on a clean exit, "timeout" if the per-instance cap is hit.
    The opencode transcript is teed to run_dir/opencode.log.
    """
    prompt = PROMPT_TEMPLATE.format(repo=spec.repo, problem=spec.problem)  # type: ignore[attr-defined]
    cmd = ["opencode", "run", "-m", model_ref, "--dir", checkout, prompt]
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, "opencode.log")
    t0 = time.perf_counter()
    with open(log_path, "w") as log:
        try:
            proc = subprocess.run(cmd, env=env, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "timeout", time.perf_counter() - t0
    wall = time.perf_counter() - t0
    return ("ok" if proc.returncode == 0 else "ok"), wall  # nonzero exit still scored


def capture_model_patch(checkout: str, spec: InstanceSpec, run_dir: str) -> str:
    """Diff the checkout vs base_commit, EXCLUDING any test files the instance's
    test_patch touches (those are externally fixed). Saved to run_dir."""
    # Exclude (a) the instance's test files — externally fixed — and (b) the
    # harness-injected lever files (opencode.json / AGENTS.md) so they never leak
    # into the scored model patch.
    test_files = _patched_files(spec.test_patch)  # type: ignore[attr-defined]
    _git(["add", "-A"], cwd=checkout)
    excludes = [f":(exclude){p}" for p in
                (*test_files, "opencode.json", "AGENTS.md", HARNESS_PROMPT_FILE)]
    diff = _git(["diff", "--cached", "--", ".", *excludes], cwd=checkout)
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

    status, ep_wall = run_opencode_episode(
        checkout, spec, model_ref, env, run_dir, timeout)

    def _result(passed, reason, f2pp=0, p2pp=0, patch_bytes=0, test_wall=0.0):
        return InstanceResult(
            instance_id=spec.instance_id, passed=passed, reason=reason,
            episode_wall_s=round(ep_wall, 1), test_wall_s=round(test_wall, 1),
            model_patch_bytes=patch_bytes,
            fail_to_pass_passed=f2pp, fail_to_pass_total=len(spec.fail_to_pass),
            pass_to_pass_passed=p2pp, pass_to_pass_total=len(spec.pass_to_pass))

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
    lines = ["# Harness-engineering experiment ledger (TODO items 11 + 14)", ""]
    if not rows:
        lines.append("_No runs recorded yet._")
        out = "\n".join(lines) + "\n"
        os.makedirs(os.path.dirname(SUMMARY_MD), exist_ok=True)
        with open(SUMMARY_MD, "w") as f:
            f.write(out)
        return out
    swebench = [r for r in rows if r.get("suite", "swebench") != "micro"]
    micro = [r for r in rows if r.get("suite") == "micro"]
    lines += _render_swebench_table(swebench)
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
    label = args.label or f"{cfg['name']}-{time.strftime('%Y%m%d-%H%M')}"

    print(f"Scoring config '{cfg['name']}' (hash {config_hash(cfg)})  "
          f"model={model_ref}  subset={len(subset)} instances\n")
    results: list[InstanceResult] = []
    for i, spec in enumerate(subset, 1):
        _hydrate(spec)
        print(f"[{i}/{len(subset)}] {spec.instance_id} …", flush=True)
        try:
            r = score_instance(spec, model_ref, cfg, args.base_url, label,
                               args.timeout)
        except Exception as e:  # noqa: BLE001 — one bad instance must not abort the run
            print(f"  error: {e}", file=sys.stderr)
            r = InstanceResult(spec.instance_id, False, f"error:{e}", 0, 0, 0,
                               0, len(spec.fail_to_pass), 0, len(spec.pass_to_pass))
        results.append(r)
        mark = "PASS" if r.passed else "FAIL"
        print(f"  -> {mark} ({r.reason})  episode={r.episode_wall_s}s  "
              f"F2P={r.fail_to_pass_passed}/{r.fail_to_pass_total}", flush=True)
        if r.reason == "oom":
            # Restart on every OOM — including the last instance — so the server
            # is left healthy for the next config in a sweep.
            restart_server(args.base_url)

    passed = sum(1 for r in results if r.passed)
    row = RunRow(
        label=label, config_name=cfg["name"], config_hash=config_hash(cfg),
        model=model_ref, subset_id=_subset_id(subset),
        sampling=cfg.get("sampling") or {}, timestamp=_now_iso(),
        instances=[asdict(r) for r in results], passed=passed, total=len(results),
        notes=cfg.get("description", ""))
    append_ledger(row)
    print(f"\nconfig '{cfg['name']}': {passed}/{len(results)} passed")
    write_summary()
    print(f"Summary table -> {SUMMARY_MD}")
    return 0


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
    rn.add_argument("--label", default=None,
                    help="run label (default: config+timestamp)")
    rn.add_argument("--base-url", default=DEFAULT_BASE_URL)
    rn.add_argument("--model", default=None,
                    help="opencode model ref (default: mlx-local/<detected>)")
    rn.add_argument("--timeout", type=float, default=DEFAULT_INSTANCE_TIMEOUT,
                    help="hard per-instance wall-clock cap (s)")
    rn.set_defaults(func=cmd_run)

    sm = sub.add_parser("summary", help="regenerate + print the markdown ledger table")
    sm.set_defaults(func=cmd_summary)

    st = sub.add_parser("selftest", help="offline sanity checks (no model needed)")
    st.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
