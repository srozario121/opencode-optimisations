# `.opencode/` — local opencode customizations

This folder customizes [opencode](https://opencode.ai) for **this repo**. It
ships custom `read` and `grep` tools that shrink the contents those built-in
tools return before they reach the model, so they cost far fewer tokens —
important when driving the local Gemma 4 QAT model on MLX (see
`docs/opencode-local.md`), which has a small, slow context. `read` filters
through `rtk` (Rust Token Killer — an external CLI; see the repo README
prerequisites); `grep` uses ripgrep directly with rtk-style reduction flags
(see the grep section for why not `rtk grep`).

```
.opencode/
  tools/
    read.ts      # custom `read` tool — shadows the built-in, filters via rtk
    grep.ts      # custom `grep` tool — shadows the built-in, reduces via ripgrep
  README.md      # this guide
```

Verified against **opencode 1.17.7**, **rtk 0.42.4**, **ripgrep 15.1.0**,
**@opencode-ai/plugin 1.15.13**, **Bun 1.3.11** on macOS arm64 (read-cap added
+ re-verified 2026-06-21; TODO item 13).

**Dependency:** `grep.ts` needs a real `rg` binary reachable from opencode's
Bun exec environment. Install once: `arch -arm64 brew install ripgrep`. (The
`rg` you see in an interactive shell may be a Claude Code function shim that
Bun can't run — `grep.ts` relies on the brew binary at `/opt/homebrew/bin/rg`.)

---

## Why a custom `read` tool

opencode's built-in `read` returns full file contents bounded only by large
defaults (≈2000 lines / 50 KB / 2000 chars-per-line). A single read can dump
tens of KB into context. opencode's config can only `allow`/`ask`/`deny` a tool
— there is **no built-in knob to shrink or compress read output**. That gap is
exactly what `rtk read --level minimal` fills (strip trailing whitespace,
truncate pathological lines, summarize overflow).

There is also **no opencode hook that runs before model inference**
([sst/opencode#21240](https://github.com/anomalyco/opencode/issues/21240)), so
token reduction has to happen at the tool I/O boundary. The cleanest, most
deterministic point is the `read` tool itself.

## How it works (the shadowing mechanism)

opencode discovers custom tools in `.opencode/tools/<name>.ts` (project) or
`~/.config/opencode/tools/<name>.ts` (global). **The filename becomes the tool
name**, and per the [custom-tools docs](https://opencode.ai/docs/custom-tools/):

> If a custom tool uses the same name as a built-in tool, the custom tool takes
> precedence.

So `tools/read.ts` transparently **replaces** the native `read` — no config
flag, no disabling step. The model keeps calling `read` and never knows.

`read.ts` accepts the same arguments the model already uses for the built-in
(`filePath`, `offset`, `limit`) and, in its `execute`, shells out to `rtk` via
Bun's shell (`Bun.$`):

- **From the top** (no offset): `rtk read --level minimal --line-numbers --max-lines N <file>` — one pass, rtk does the filtering, numbering, and line cap. `N` is the hard cap (below), **always set** now.
- **With an offset**: `rtk` has no `offset` flag, so the tool slices the window
  itself (also bounded by the hard cap), pipes it through
  `rtk read --level minimal /dev/stdin < <blob>`, and appends a footer with the
  absolute range + the offset to continue from. Line numbers shown by rtk in this
  branch are **window-relative**; the footer is the source of truth for absolute
  position.

### Hard read cap (TODO item 13)

Both paths above enforce a **mandatory** cap so a single large file can't blow a
turn's prompt past the local Gemma stack's ~40–50K-token Metal-OOM ceiling
(`docs/opencode-local.md`). Two reasons it's two-dimensional:

- **Line cap** (`READ_MAX_LINES`, default **1500**) — the lever both code paths
  share (top-of-file `rtk --max-lines`; offset-path manual slice). It was
  previously only applied when the model passed `limit`, so a no-`limit`
  top-of-file read was **uncapped**; it is now always applied, and a model `limit`
  larger than the cap is **clamped** down to it.
- **Column cap** (`READ_MAX_COLUMNS`, default **200**) — MEASURED: rtk 0.42.4 does
  **not** truncate long lines (neither `minimal` nor `aggressive`), so a line cap
  alone is not OOM-safe (worst-case tokens scale with line *width*). The tool
  truncates each line to this width with a `…[+N chars]` elision marker. Real repo
  source has a p99 width of ~100 cols, so 200 leaves ordinary code untouched while
  bounding minified/data/JSON lines. Worst-case bound ≈ `lines × cols`
  (~40.5K tokens at the defaults — inside the ceiling).

Every truncated read carries the **continuation footer**
(`(rtk: lines 1-1500 of 5001; capped at 1500 lines, use offset=1501 to continue)`)
so the model pages on with `offset=`. `scripts/mlx.sh opencode-config` writes the
defaults into `~/.config/opencode/mlx-read-cap.env` (disable with `MLX_READ_CAP=0`,
tune with `MLX_READ_MAX_LINES` / `MLX_READ_MAX_COLUMNS`); the cap **logic** stays
here in `read.ts`.

### Safety: secret-file guard

Shadowing the built-in `read` **bypasses opencode's default `*.env` deny rule**,
so `read.ts` re-implements it: any file whose name matches `.env` /
`.env.<suffix>` is refused with an error, except the `*.example` / `*.sample` /
`*.template` escape hatches. (`rtk read` itself does *not* redact secrets —
verified — so this guard is load-bearing. Keep it.)

### Resilience: rtk-missing fallback

If `rtk` is absent or errors, `Bun.$` throws `ShellError`; the tool catches it
and falls back to a plain line-numbered, line-capped read (`rawRead`). The read
tool never hard-fails the agent just because rtk is unavailable.

## The `grep` tool

`grep.ts` shadows the built-in `grep` the same way (same args: `pattern`,
`path`, `include`). opencode's built-in grep returns `file:line:matching-line`
across the whole tree — token-heavy when matches are many or lines are long,
and (like read) there's no built-in knob to trim it.

**Why ripgrep, not `rtk grep`:** on this machine `rtk grep` is degenerate — it
returns `"N matches in 0 files"` with no actual matches (it shells to BSD `grep`
and mis-parses the output), so it can't back a useful grep tool. opencode's own
bundled ripgrep isn't reachable from a custom tool's Bun environment either. So
`grep.ts` shells a real `rg` (installed via brew) and applies the reduction
`rtk grep` is *meant* to do, using rg's own flags:

- `--max-columns 200 --max-columns-preview` — truncate long lines to a preview
  (the main token sink: minified/data lines), keeping regex semantics identical
  to the built-in grep (same engine).
- total result cap (default 200) — excess matches are summarized as
  `... [N more matches omitted]` rather than dumped.
- `--line-number --color never` and `--glob <include>` for the `include` filter.

**No-match handling:** `rg` exits `1` when nothing matches (not an error), so the
tool uses `.quiet().nothrow()` and returns `"No matches for /pattern/…"`; a real
`rg` failure (exit ≥2, e.g. bad regex) is surfaced as an error.

**Secret guard:** `rg` skips hidden files by default, so `.env` is normally not
searched — but an explicit `path` to a secret file still would be, so `grep.ts`
re-uses the same `.env*` refusal as `read.ts`.

## Configuration

| Knob | Default | Effect |
|------|---------|--------|
| `RTK_READ_LEVEL` env var | `minimal` | rtk filter level: `none` (full content), `minimal`, or `aggressive`. `aggressive` can empty some files — rtk then prints a stderr warning and shows raw content, so it is never silently lossy. |
| `READ_MAX_LINES` env var | `1500` | Hard line cap per `read` call (both paths). A model `limit` larger than this is clamped; truncated reads carry the continuation footer. |
| `READ_MAX_COLUMNS` env var | `200` | Hard per-line column cap (rtk does not truncate long lines in 0.42.4). Truncated lines get a `…[+N chars]` marker. `0` disables column truncation. |
| `RTK_GREP_MAX_COLUMNS` env var | `200` | Truncate each matching line to this many columns (rg `--max-columns`). |
| `RTK_GREP_MAX_RESULTS` env var | `200` | Cap total matches returned; the rest are summarized as an omitted-count note. |

Set them per-session, e.g.:

```bash
RTK_READ_LEVEL=aggressive opencode    # maximize read reduction
RTK_READ_LEVEL=none       opencode    # effectively disable read filtering (still adds line numbers)
RTK_GREP_MAX_RESULTS=50   opencode    # tighter grep cap for a small context window
```

## Verifying it's active

```bash
# 1. dependencies on PATH
rtk --version          # -> rtk 0.42.4   (read.ts)
rg  --version          # -> ripgrep 15.x (grep.ts; brew binary at /opt/homebrew/bin/rg)

# 2. confirm opencode loaded the overrides (look for OUR descriptions):
opencode serve --port 7531 >/tmp/oc.log 2>&1 &
curl -s "http://127.0.0.1:7531/experimental/tool?provider=mlx-local&model=gemma" \
  | python3 -c "import sys,json; [print(t['id'],'::',t['description'][:70]) for t in json.load(sys.stdin) if t['id'] in ('read','grep')]"
kill %1   # both 'read' and 'grep' should show a built-in entry AND our rtk/ripgrep one

# 3. Inside opencode, watch token usage drop on read/grep-heavy sessions:
opencode stats --tools
```

If the import `@opencode-ai/plugin` fails to resolve for a project-local tool on
your opencode version, either (a) install it locally
(`bun add -d @opencode-ai/plugin@1.15.13`) or (b) move `read.ts` to the global
`~/.config/opencode/tools/` dir, where the package is already present
(`~/.config/opencode/package.json`).

## Reverting / disabling

- **Disable read filtering, keep the tool**: `RTK_READ_LEVEL=none`.
- **Loosen grep reduction**: raise `RTK_GREP_MAX_COLUMNS` / `RTK_GREP_MAX_RESULTS`.
- **Restore a built-in entirely**: delete `.opencode/tools/read.ts` or
  `grep.ts` (and the matching symlink in `~/.config/opencode/tools/`). opencode
  falls back to its native tool immediately.

## How to add another rtk-backed tool (recipe)

The same pattern generalizes to any token-heavy built-in (e.g. `list`, `grep`)
or a brand-new tool:

1. Create `.opencode/tools/<name>.ts`. The filename is the tool name; reuse a
   built-in name to shadow it, or pick a new name to add a tool.
2. ```ts
   import { tool } from "@opencode-ai/plugin"
   export default tool({
     description: "…what the model should know…",
     args: { path: tool.schema.string().describe("…") },   // Zod-based schema
     async execute(args) {
       const out = await Bun.$`rtk <subcommand> ${args.path}`.text()
       return out.trim()
     },
   })
   ```
3. If you shadow a built-in, **re-implement any safety gate** the built-in gave
   you for free (e.g. the `.env` deny on `read`), and **match the built-in's
   arg names** so the model's existing calls keep working.
4. Wrap the `Bun.$` call in `try/catch` with a non-rtk fallback so a missing
   `rtk` never breaks the agent.

> Alternative considered — **plugin `tool.execute.after` hook** (mutate
> `output.output` after the built-in runs). Rejected for this use case: it has a
> documented, reproducible failure mode where hooks silently never fire even
> though the plugin loads
> ([rtk-ai/rtk#1706](https://github.com/rtk-ai/rtk/issues/1706)), doesn't work
> for MCP tools, and may be ignored on the UI path. The custom-tool shadow is
> deterministic.

## Version-sensitivity

opencode moves fast. Re-verify on upgrade:

- The plural `tools/` dir is canonical; singular `tool/` works only as a
  backwards-compat alias.
- The built-in read constants and their enforcement have regressed between
  releases ([#27864](https://github.com/anomalyco/opencode/issues/27864)) — but
  this tool doesn't depend on them.
- `Bun.$` has no `.stdin()` method; stdin is supplied via redirection
  `< ${new Blob([…])}` (used in the offset path).
