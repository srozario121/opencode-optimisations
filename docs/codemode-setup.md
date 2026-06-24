# Enabling `codemode` with opencode

`codemode` is a custom opencode tool that lets the model run **one Python program**
that calls filesystem host-tools and chains many operations in a single tool call,
returning only the consolidated result — instead of one read/grep/list call per
operation. On the local Gemma-4-E4B stack this batches multi-file work into one
round-trip.

> **Read this first:** `docs/sandbox-codeexec-research.md` is the full evidence base.
> The short version of *when it helps* is in [When to use it](#when-to-use-it) below —
> the win is real but **task-dependent**, not a blanket speedup.

## Components

| File | Role |
|---|---|
| `.opencode/tools/codemode.ts` | The opencode custom tool. Discovered by opencode; shells to the executor via `Bun.spawn`. |
| `scripts/codemode_exec.py` | The executor. Runs the model's Python in a restricted sandbox bound to **real** filesystem host-tools, returns a JSON envelope. |

The model writes Python (which it does reliably — see the research doc), executed
**out-of-process** by the Python executor. This bridges opencode's TS world to the
Python the model emits.

## Quick start

`codemode` is already wired into this repo — opencode discovers any
`.opencode/tools/<name>.ts` when run from the project. No install is needed for the
default engine (it is stdlib-only).

1. **Confirm opencode loads it:**
   ```bash
   opencode serve --port 7531 >/tmp/oc.log 2>&1 &
   curl -s "http://127.0.0.1:7531/experimental/tool?provider=mlx-local&model=gemma" \
     | python3 -c "import sys,json; print('codemode' in [t['id'] for t in json.load(sys.stdin)])"
   kill %1
   ```
   Expect `True`.

2. **Use it in a session** (the model invokes it natively):
   ```bash
   opencode run "Use codemode to count the total lines across all .py files under scripts/ in one call."
   ```

To enable it for **every** project, symlink the tool into the global config dir
(opencode also discovers `~/.config/opencode/tools/<name>.ts`):
```bash
ln -s "$PWD/.opencode/tools/codemode.ts" ~/.config/opencode/tools/codemode.ts
```
Note the executor path is resolved relative to the tool file (or via `CODEMODE_EXEC`,
below), so a global symlink still finds `scripts/codemode_exec.py` in this repo.

## Host-tools available inside the code

The model's program may call these (already in scope; read-only by default):

| Function | Returns |
|---|---|
| `read_file(path)` | full file text |
| `read_lines(path, start=1, end=None)` | lines `start`..`end`, **1-indexed inclusive**; `end=None` = EOF |
| `list_files(dir=".")` | file paths directly under a dir |
| `glob(pattern)` | glob matches (supports `**`) |
| `grep(pattern, path=".")` | `"relpath:lineno:line"` matches (Python regex) |
| `bash(cmd)` *(opt-in)* | stdout of a shell command |
| `write_file(path, text)` *(opt-in)* | bytes written |

The program assigns its answer to a variable named `result`. Paths cannot escape the
project root.

## Configuration (environment variables)

All optional; sensible defaults.

| Env var | Default | Purpose |
|---|---|---|
| `CODEMODE_ENGINE` | `exec` | Sandbox engine: `exec` (restricted CPython, stdlib-only) or `monty` (real Pydantic Monty VM — see below). |
| `CODEMODE_ALLOW_BASH` | unset | Set `=1` to expose `bash(cmd)` inside the sandbox. |
| `CODEMODE_ALLOW_WRITE` | unset | Set `=1` to expose `write_file(path, text)`. |
| `CODEMODE_ROOT` | opencode's cwd / `--dir` | Root the host-tools are bound to. |
| `CODEMODE_EXEC` | repo-relative to the tool | Absolute path to `codemode_exec.py` (used when the tool is installed outside the repo). |
| `CODEMODE_PYTHON` | repo `.venv` else `python3` | Python interpreter to run the executor. |

Example — enable bash + the Monty engine for a session:
```bash
CODEMODE_ALLOW_BASH=1 CODEMODE_ENGINE=monty opencode run "..."
```

## Optional: the Monty engine

The default `exec` engine needs no third-party packages. The `monty` engine runs the
code in the real **Pydantic Monty** sandbox VM (in-process isolation). Install it:

```bash
uv sync --group monty        # installs pydantic-monty (pinned <0.1, it is alpha)
CODEMODE_ENGINE=monty opencode run "..."
```

> **Dialect caveat (measured):** Monty v0.0.18 implements a Python *subset* and rejects
> some common idioms — notably `max(items, key=lambda x: <calls a host-tool>)` and
> `dict.get()`. On this stack Gemma writes plainer explicit loops and is unaffected, but
> if you adopt Monty, prefer steering the model toward explicit loops. Details:
> `docs/sandbox-codeexec-research.md` (Empirical addendum 2).

## When to use it

From the production A/B (`docs/sandbox-codeexec-research.md`, Empirical addendum 5):

- ✅ **Worth it** for round-trip-heavy multi-file gather / scan / count / aggregate that
  the model would otherwise do as N separate read/grep calls (measured −50% calls,
  −20–56% wall-clock).
- ➖ **Marginal** when the model already self-batches via `bash` (e.g. one `wc -l`) — on
  this stack `bash` is a competing, simpler "code mode" the model often picks unprompted.
- ❌ **No help** on the degenerate-loop failure (the model churning without terminating);
  `codemode` only helps when the model is *productively* making tool calls.

It never lost in testing, so keeping it available is low-risk — but don't expect it to
move the headline pass-rate on its own.

## Testing

```bash
# executor sandbox (offline, no opencode/model):
.venv/bin/python scripts/codemode_exec.py --selftest

# run a code block by hand against the repo:
echo "result = sum(len(read_file(p).splitlines()) for p in glob('scripts/*.py'))" \
  | .venv/bin/python scripts/codemode_exec.py --root .

# the A/B harnesses behind the research findings:
.venv/bin/python scripts/codegen_probe.py selftest        # code-gen pass@1 probe
.venv/bin/python scripts/codemode_ab.py selftest          # flat-ReAct vs code-mode (mock tools)
.venv/bin/python scripts/codemode_prod_ab.py selftest     # production A/B (real opencode)
```

## Security note

`codemode` executes model-generated code. The sandbox restricts the namespace to the
injected host-tools plus a safe builtin set (no `import`/`open`/`eval`), and paths can't
escape the project root — but exec-based sandboxes are a **usability** boundary, not a
hard security boundary. This matches the threat model: a **single-user, local, offline**
dev stack. `bash`/`write_file` are **off by default**; enable them only when you trust
the session. For stronger isolation use `CODEMODE_ENGINE=monty` (zero-access-by-default
VM), within its dialect limits.
