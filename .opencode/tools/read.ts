/**
 * Custom `read` tool — shadows opencode's built-in read and routes file
 * contents through `rtk read` (Rust Token Killer) so they are filtered down
 * before they reach the model's context. See ../README.md for the full guide.
 *
 * opencode keys tools by filename, and a custom tool that shares a built-in
 * name takes precedence over it — so this file replaces the native read tool
 * wherever opencode is run from this project.
 *   docs: https://opencode.ai/docs/custom-tools/
 *
 * Verified against: opencode 1.17.7, rtk 0.42.4, @opencode-ai/plugin 1.15.13.
 */
import { tool } from "@opencode-ai/plugin"

// rtk filter level: "none" (full content) | "minimal" | "aggressive".
// "minimal" is the safe default — it strips trailing whitespace and truncates
// pathological long lines without dropping real content. Override per-session
// with RTK_READ_LEVEL. ("aggressive" can empty-out some files; rtk then falls
// back to raw content with a stderr warning, so it is never silently lossy.)
const LEVEL = process.env.RTK_READ_LEVEL ?? "minimal"

// TODO item 13, thread 3 — hard read cap (the destination resolved by the item).
// The built-in read and the previous version of this tool only bounded output
// when the model PASSED `limit`; a top-of-file read with no `limit` was uncapped
// (rtk shrinks tokens but does not cap line count), so one large file could blow
// a turn's prefill past the ~40–50K-token Metal-OOM cliff on the 16 GB M1
// (item 9). This makes a maximum line window MANDATORY on BOTH enforcement sites
// — the top-of-file `rtk --max-lines` path and the offset/limit manual-slice path
// — so the model can never exceed it by either route, regardless of whether it
// supplied `limit`. The unit is LINES (not tokens): rtk's token-filtering already
// shrinks the *content* of each line, and a line cap is the one lever both code
// paths share, so a single line cap gives a coherent effective ceiling across
// both sites without double-counting rtk's reduction. The continuation footer is
// preserved on every truncated read so the model can page the rest with `offset=`.
// 1500 lines × the rtk-filtered token-per-line cost stays well under the OOM
// ceiling for ordinary source (see docs/opencode-local.md, item 13). Override the
// default with READ_MAX_LINES; the generator writes its chosen default into the
// env when launching opencode (scripts/mlx.sh opencode-config). A larger explicit
// `limit` from the model is still clamped to this cap (belt-and-suspenders with
// the resident read-range rules, which stay in `instructions`).
const MAX_LINES_DEFAULT = 1500
const READ_MAX_LINES = (() => {
  const n = Number(process.env.READ_MAX_LINES)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : MAX_LINES_DEFAULT
})()

// MEASURED (item 13): rtk 0.42.4's `minimal` AND `aggressive` levels do NOT
// truncate long lines (a 1500×3000-char file passed through at ~564K tokens),
// contradicting the README's "truncate pathological long lines" claim for this
// version. So a line cap alone is NOT an OOM-safe ceiling — worst-case tokens
// scale with line WIDTH, not just count. We therefore pair the line cap with a
// per-line column cap so the effective ceiling is bounded in BOTH dimensions:
// worst-case chars <= READ_MAX_LINES × READ_MAX_COLUMNS. Real repo source has a
// p99 line width of ~100 cols (max 888), so 200 cols leaves all ordinary code
// untouched while bounding minified/data/JSON lines. MEASURED worst case (1500
// lines all at full width, the minified.txt fixture): cols=250 → ~49.9K tokens
// (upper edge of the ceiling); cols=200 → ~40.5K tokens (clearly inside it). The
// realistic worst case (a dense 180-col file) is ~35K. 200 also matches the grep
// tool's RTK_GREP_MAX_COLUMNS default, so it is the adopted default. Override with
// READ_MAX_COLUMNS.
const MAX_COLUMNS_DEFAULT = 200
const READ_MAX_COLUMNS = (() => {
  const n = Number(process.env.READ_MAX_COLUMNS)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : MAX_COLUMNS_DEFAULT
})()

// The effective per-read line window: the model's `limit` if it asked for fewer
// lines, otherwise the hard cap. Always returns a positive integer <= the cap.
function effectiveLimit(limit?: number): number {
  if (limit && limit > 0) return Math.min(limit, READ_MAX_LINES)
  return READ_MAX_LINES
}

// Bound each line to the column cap (rtk does not in this version). Appends a
// short elision marker so a truncated line is visibly partial to the model.
function clampColumns(text: string): string {
  if (READ_MAX_COLUMNS <= 0) return text
  let truncated = false
  const out = text
    .split("\n")
    .map((l) => {
      if (l.length <= READ_MAX_COLUMNS) return l
      truncated = true
      return l.slice(0, READ_MAX_COLUMNS) + ` …[+${l.length - READ_MAX_COLUMNS} chars]`
    })
    .join("\n")
  return truncated ? out : text
}

// Built-in read denies `*.env` by default; shadowing it drops that gate, so we
// re-implement secret-file protection here. Allow the documented *.example /
// *.sample / *.template escape hatches.
function isSecretFile(filePath: string): boolean {
  const base = (filePath.split("/").pop() ?? filePath).toLowerCase()
  if (/\.(example|sample|template)$/.test(base)) return false
  return /^\.env(\..+)?$/.test(base)
}

// Fallback used only if rtk is missing or errors — never let the read tool
// hard-fail the agent. Mirrors the built-in's line-numbered, capped output.
async function rawRead(
  filePath: string,
  start: number,
  limit?: number,
): Promise<string> {
  const lines = (await Bun.file(filePath).text()).split("\n")
  const end = limit ? start + limit : Math.min(lines.length, start + 2000)
  return lines
    .slice(start, end)
    .map((l, i) => `${start + i + 1} | ${l}`)
    .join("\n")
}

export default tool({
  description:
    "Read a file with token-reduced output via rtk. Returns line-numbered " +
    "contents filtered through `rtk read` to minimize context usage. Use " +
    "`offset` (1-based start line) and `limit` (max lines) to page through " +
    "large files; the output footer tells you the offset to continue from.",
  args: {
    filePath: tool.schema
      .string()
      .describe("Absolute path to the file to read"),
    offset: tool.schema
      .number()
      .optional()
      .describe("1-based line number to start reading from (large files)"),
    limit: tool.schema
      .number()
      .optional()
      .describe("Maximum number of lines to read"),
  },
  async execute(args) {
    const { filePath, offset, limit } = args

    if (isSecretFile(filePath)) {
      throw new Error(`Refusing to read potential secret file: ${filePath}`)
    }

    const start = offset && offset > 1 ? offset - 1 : 0
    // Single effective window applied identically on BOTH paths (thread-3 cap).
    const cap = effectiveLimit(limit)

    // Total line count, used to decide whether to append the continuation footer
    // on the top-of-file path too (cheap; one read of a local file).
    let total: number
    try {
      total = (await Bun.file(filePath).text()).split("\n").length
    } catch {
      throw new Error(`File not found or unreadable: ${filePath}`)
    }

    // Common path — read from the top. Hand the file to rtk with the hard line
    // cap ALWAYS set (it was previously only set when the model passed `limit`,
    // leaving top-of-file reads uncapped). Append a continuation footer when the
    // file is longer than the window so the model knows to page on with `offset=`.
    if (start === 0) {
      const shown = Math.min(cap, total)
      const footer =
        shown < total
          ? `\n(rtk: lines 1-${shown} of ${total}; capped at ${cap} lines, use offset=${shown + 1} to continue)`
          : `\n(rtk: lines 1-${shown} of ${total})`
      const flags = ["--level", LEVEL, "--line-numbers", "--max-lines", String(cap)]
      try {
        const out = await Bun.$`rtk read ${flags} ${filePath}`.text()
        return clampColumns(out.trim()) + footer
      } catch {
        return clampColumns(rawRead(filePath, 0, cap)) + footer
      }
    }

    // Offset path — rtk has no offset flag, so slice the window ourselves and
    // pipe it through rtk via stdin. The slice is bounded by the SAME hard cap so
    // the model cannot exceed the ceiling by paging with a huge `limit` either.
    // Numbers shown by rtk are relative to the window; the footer records the
    // absolute range and continuation offset.
    const lines = (await Bun.file(filePath).text()).split("\n")
    const end = start + cap
    const window = lines.slice(start, end).join("\n")
    const shown = Math.min(end, lines.length)
    const footer =
      shown < lines.length
        ? `\n(rtk: lines ${start + 1}-${shown} of ${lines.length}; capped at ${cap} lines, use offset=${shown + 1} to continue)`
        : `\n(rtk: lines ${start + 1}-${shown} of ${lines.length})`

    try {
      const filtered =
        await Bun.$`rtk read --level ${LEVEL} /dev/stdin < ${new Blob([window])}`.text()
      return clampColumns(filtered.trim()) + footer
    } catch {
      return clampColumns(await rawRead(filePath, start, cap)) + footer
    }
  },
})
