/**
 * Custom `grep` tool — shadows opencode's built-in grep and returns
 * token-reduced content search results. See ../README.md for the full guide.
 *
 * Backend note: this uses ripgrep (`rg`) DIRECTLY, not `rtk grep`. On this
 * machine `rtk grep` is degenerate (it returns "N matches in 0 files" with no
 * actual matches), and opencode's exec environment has no other `rg` reachable
 * from Bun — so we install ripgrep (brew) and apply the token-reduction that
 * `rtk grep` is *supposed* to do via rg's own flags: `--max-columns` truncates
 * pathological long lines, and a total result cap trims runaway match counts.
 * Using rg keeps regex semantics identical to opencode's built-in grep.
 *
 * opencode keys tools by filename; a custom tool sharing a built-in name takes
 * precedence, so this file replaces the native grep tool.
 *   docs: https://opencode.ai/docs/custom-tools/
 *
 * Verified against: opencode 1.17.7, ripgrep 15.1.0, @opencode-ai/plugin
 * 1.15.13, Bun 1.3.11.
 */
import { tool } from "@opencode-ai/plugin"

// Truncate any single matching line to this many columns (the main token sink
// — minified/data lines). rg shows a preview up to the limit, then elides.
const MAX_COLUMNS = Number(process.env.RTK_GREP_MAX_COLUMNS ?? "200")
// Cap total matches returned; excess is summarized rather than dumped.
const MAX_RESULTS = Number(process.env.RTK_GREP_MAX_RESULTS ?? "200")

// rg skips hidden files by default, so `.env` is normally safe — but an
// explicit path to a secret file is still searched. Guard it, mirroring read.ts.
function isSecretFile(p: string): boolean {
  const base = (p.split("/").pop() ?? p).toLowerCase()
  if (/\.(example|sample|template)$/.test(base)) return false
  return /^\.env(\..+)?$/.test(base)
}

export default tool({
  description:
    "Search the text inside files and get back the matching lines with their " +
    "line numbers. Use this FIRST to locate code before reading or editing it. " +
    "Output is `file:line:match`, one match per line, with long lines truncated " +
    "and the total capped to stay small. REQUIRED: `pattern` — the text or " +
    "ripgrep regex to find (e.g. `pattern=\"def target_func\"` or " +
    "`pattern=\"SENTINEL_TOKEN\"`). OPTIONAL: `path` to limit the search to a " +
    "file or directory (default: the whole project), and `include` to filter " +
    'files by glob (e.g. `include="*.py"`). Worked example: to find where a ' +
    'function is defined, call grep with pattern="def my_func".',
  args: {
    pattern: tool.schema
      .string()
      .describe("REQUIRED. The text or ripgrep regular expression to search " +
        'for, e.g. "def target_func" or "MAX_RETRIES". Plain words work as-is.'),
    path: tool.schema
      .string()
      .optional()
      .describe("OPTIONAL. A file or directory to restrict the search to. " +
        "Omit to search the entire current directory."),
    include: tool.schema
      .string()
      .optional()
      .describe('OPTIONAL. A glob to filter which files are searched, ' +
        'e.g. "*.py" for Python files or "*.{ts,tsx}" for TypeScript.'),
  },
  async execute(args) {
    const { pattern, path, include } = args
    const target = path && path.length > 0 ? path : "."

    if (isSecretFile(target)) {
      throw new Error(`Refusing to grep potential secret file: ${target}`)
    }

    const flags = [
      "--line-number",
      "--color",
      "never",
      "--max-columns",
      String(MAX_COLUMNS),
      "--max-columns-preview",
    ]
    if (include) flags.push("--glob", include)

    // `--` ends option parsing so a pattern starting with `-` is still treated
    // as the search term. `.nothrow()` because rg exits 1 on "no matches".
    const r = await Bun.$`rg ${flags} -- ${pattern} ${target}`.quiet().nothrow()

    if (r.exitCode === 1) {
      return `No matches for /${pattern}/ in ${target}.`
    }
    if (r.exitCode !== 0) {
      const err = r.stderr.toString().trim().split("\n")[0] ?? "unknown error"
      throw new Error(`ripgrep failed (exit ${r.exitCode}): ${err}`)
    }

    const lines = r.stdout.toString().trimEnd().split("\n")
    if (lines.length <= MAX_RESULTS) {
      return lines.join("\n")
    }
    const omitted = lines.length - MAX_RESULTS
    return (
      lines.slice(0, MAX_RESULTS).join("\n") +
      `\n... [${omitted} more matches omitted — narrow the pattern or set \`include\`]`
    )
  },
})
