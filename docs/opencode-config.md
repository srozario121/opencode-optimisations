# opencode config best practices (local Gemma / MLX)

Best-practice guidance for the [opencode](https://opencode.ai) config file when
driving the **local Gemma 4 QAT model on MLX** (see
[`opencode-local.md`](opencode-local.md) for the server setup). Focus: a
small, slow, on-device context window — so every recommendation below leans
toward token frugality and local/self-hosted providers.

Findings were gathered by a multi-source, adversarially-verified research pass
(June 2026) against the opencode docs, the `sst/opencode` source, and issue
tracker. opencode moves fast — re-verify on upgrade (see
[Version-sensitivity](#version-sensitivity)).

## Config file location & precedence

opencode reads `opencode.json` / `opencode.jsonc` from the **project** dir, then
the **global** `~/.config/opencode/opencode.json`; project keys override global.
Always set `"$schema": "https://opencode.ai/config.json"` for editor validation.

This machine's global config lives at `~/.config/opencode/opencode.json` and
already declares the `mlx-local` provider.

---

## 1. Local provider — declare limits explicitly

Custom providers (anything via `@ai-sdk/openai-compatible`) do **not** fetch
token limits from models.dev the way first-class providers do. You **must**
declare `limit.context` and `limit.output` per model, or opencode errors
(`maxOutputTokens must be >= 1`, [#22253]) or silently defaults to 32000/64000
([#1735]).

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "mlx-local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local MLX (Gemma 4 QAT)",
      "options": { "baseURL": "http://127.0.0.1:8080/v1", "apiKey": "not-needed" },
      "models": {
        "<model-id>": { "limit": { "context": 32768, "output": 8192 } }
      }
    }
  },
  "model": "mlx-local/<model-id>",
  "small_model": "mlx-local/<model-id>"
}
```

- Set `limit.context` to what the MLX server actually serves; keep it
  conservative — on a 16 GB M1 the limiter is slow prefill + OOM, not the cache.
- ⚠️ Some versions don't forward `baseURL` for custom providers ([#5674]) —
  confirm requests actually hit `127.0.0.1` (watch the MLX/proxy log).

**Built-in providers need no `npm`/`baseURL` block — only an options override.**
A first-class provider opencode already knows (e.g. `opencode`, reaching the zen
gateway) is referenced as `provider/model` directly; you only declare a `provider`
entry to *override* its model options. The harness's `external_provider` gate (TODO
item 22) uses exactly this: with the flag on, `harness_eval.apply_levers` writes **no
`mlx-local` block and no local `baseURL`**, just the sampling/limit override under the
built-in provider —

```jsonc
{
  "provider": { "opencode": { "models": { "big-pickle": {
    "limit": { "context": 32768, "output": 4096 },
    "options": { "temperature": 0.0 }
  } } } },
  "model": "opencode/big-pickle",
  "small_model": "opencode/big-pickle"
}
```

— so opencode resolves the ref through its own provider with the local MLX stack
off. See *Online harness-soundness control* in `docs/opencode-local.md`.

*Sources: [providers docs], [#22253], [#1735], [#5674]*

---

## 2. Compaction — managing the small context window

A **hidden built-in `compaction` agent** auto-summarizes long sessions (runs
automatically, not selectable in the UI). It's tuned by a top-level object:

```jsonc
{
  "compaction": {
    "auto": true,      // default — compact when context fills
    "prune": true,     // default false — drop OLD TOOL OUTPUTS to save tokens
    "reserved": 2000   // token buffer so compaction itself doesn't overflow
  }
}
```

For a small local window the two levers that matter:
- **`prune: true`** — sheds stale tool output, the biggest avoidable token sink.
- **`reserved`** — size it to your window so the summarization call has room
  (no documented numeric default; pick a few hundred–low-thousand for ~32K).

Disable autocompaction with `compaction.auto: false` or env
`OPENCODE_DISABLE_AUTOCOMPACT`.

*Sources: [config docs], [agents docs], [cli docs]*

---

## 3. Continual learning — rules, instructions, commands

**Rules files** load in three tiers (first match wins per category):
1. Local `AGENTS.md` / `CLAUDE.md`, traversing up from cwd
2. Global `~/.config/opencode/AGENTS.md`
3. Claude Code's `~/.claude/CLAUDE.md`

➡️ This repo's `~/.claude/CLAUDE.md` is **already auto-loaded** by opencode as
the lowest tier. Put repo-specific guidance in a project `AGENTS.md`.

**`instructions`** — extra files by path, glob, or remote URL (5s fetch timeout):

```jsonc
{ "instructions": ["CONTRIBUTING.md", "docs/*.md", "packages/*/AGENTS.md"] }
```

**Custom commands** — codify repeatable, low-token playbooks as markdown in
`~/.config/opencode/commands/` (global) or `.opencode/commands/` (project), or
inline via the `command` key. Templating: `$ARGUMENTS` / positional `$1 $2`,
`` !`cmd` `` to inject shell output, `@file` to include a file. Each command can
pin its own `model`.

*Sources: [rules docs], [commands docs], [config docs]*

---

## 4. Memory / storage layout

- Data dir (macOS/Linux): `~/.local/share/opencode/`
- Per-project partition: inside a git repo → `./<project-slug>/storage/`;
  outside → `./global/storage/`
- Logs: `~/.local/share/opencode/log/`, timestamped, **only the most recent 10
  kept**

⚠️ Don't depend on exact internal sub-paths — verification *refuted* specific
claims about `storage/message/...`, `session-metadata/...`, and an
`OPENCODE_DATA_DIR` env var. The stable facts are the data-dir root, the
git-vs-global split, and log retention; everything below `storage/` varies by
version (newer builds add a SQLite `opencode.db`).

*Source: [troubleshooting docs]*

---

## 5. Observability (local dev)

Native tooling — there is **no built-in OpenTelemetry**:
- `opencode stats` — token usage & cost (`--days`, `--tools`, `--models`,
  `--project`). The key low-budget token-watch tool.
- `--log-level DEBUG|INFO|WARN|ERROR` and `--print-logs` (to stderr).
- `opencode debug …` — `config`, `rg`, `agent <name>`, `paths`, etc.
- The headless server exposes `/experimental/tool` (lists loaded tools — useful
  to confirm custom-tool overrides; see [`.opencode/README.md`](../.opencode/README.md)).

Optional OTLP export via a third-party plugin (native OTEL is open request
[#21240]):

```jsonc
{ "plugin": ["@devtheops/opencode-plugin-otel"] }
```
Runtime via env: `OPENCODE_ENABLE_TELEMETRY=1`,
`OPENCODE_OTLP_ENDPOINT=http://localhost:4317` (include the scheme).

*Sources: [cli docs], [troubleshooting docs], [opencode-plugin-otel]*

---

## 6. Efficient token usage

| Lever | Setting |
|---|---|
| Route lightweight tasks (title gen) to a cheap model | `small_model` → the local provider (prevents a remote fallback) |
| Per-agent routing | `agent.<name>.model` (cheap for `plan`, capable for `build`) |
| Shed stale tool output | `compaction.prune: true` |
| Avoid mid-compaction overflow | tune `compaction.reserved` |
| Cap generation | low `limit.output` |
| Codify repeatable flows | custom commands with pinned `model` |
| **Reduce tool output at the I/O boundary** | custom `read`/`grep` tools — see [`.opencode/README.md`](../.opencode/README.md) |

For a single local model, point **both** `model` and `small_model` at the same
`mlx-local/...` id so title generation never reaches for a remote "cheaper"
model. There is **no hook before model inference** ([#21240]), so context
minimization must happen at the tool I/O boundary — which is exactly what the
repo's custom `read.ts` (via rtk) and `grep.ts` (via ripgrep) do.

---

## Version-sensitivity

Verified live as of **June 2026** against **opencode 1.17.7**. opencode is
fast-moving — re-check on upgrade:

- Config keys (`compaction.*`, `instructions`, `small_model`, `provider.limit`)
  could change.
- The custom-provider `baseURL` forwarding bug ([#5674]) and the
  limit-required validation ([#22253], [#1735]) are version-sensitive — test on
  your installed version.
- Internal storage sub-paths below `storage/` are **not** load-bearing.
- OpenTelemetry is plugin-only, not native.
- `reserved` has no documented numeric default (examples show 10000).

<!-- references -->
[providers docs]: https://opencode.ai/docs/providers/
[config docs]: https://opencode.ai/docs/config/
[agents docs]: https://opencode.ai/docs/agents/
[cli docs]: https://opencode.ai/docs/cli/
[rules docs]: https://opencode.ai/docs/rules/
[commands docs]: https://opencode.ai/docs/commands/
[troubleshooting docs]: https://opencode.ai/docs/troubleshooting/
[opencode-plugin-otel]: https://github.com/DEVtheOPS/opencode-plugin-otel
[#1735]: https://github.com/anomalyco/opencode/issues/1735
[#5674]: https://github.com/anomalyco/opencode/issues/5674
[#21240]: https://github.com/anomalyco/opencode/issues/21240
[#22253]: https://github.com/anomalyco/opencode/issues/22253
