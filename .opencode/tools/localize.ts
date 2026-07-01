/**
 * Custom `localize` tool — item 31.3 (tailored tool for context discovery).
 *
 * The weak Gemma-4-E4B spends 5-8 slow tool rounds (~60-120s each on the 16 GB M1)
 * reproducing a bug and hunting the right file by hand — and does it with high
 * variance (often anchoring on the WRONG file). This tool collapses that whole loop
 * into ONE deterministic call: it runs the failing snippet IN THE INSTANCE VENV (so
 * `import <repo>` works), then returns the exception, the recursion cycle (the repo
 * frames that repeat), and a source window around each — so the model gets the exact
 * editable source in one shot instead of orchestrating reproduce -> read-traceback ->
 * read-each-file across many rounds.
 *
 * Unlike `codemode.ts` (an import-LESS usability sandbox), this runs real Python in
 * the repo's own venv via `scripts/localize_repro.py`. Wired only when the harness
 * sets LOCALIZE_HELPER + HARNESS_VENV_PY (gated; off in baseline runs).
 */
import { tool } from "@opencode-ai/plugin"

const HELPER = process.env.LOCALIZE_HELPER ?? ""
const PY =
  process.env.HARNESS_VENV_PY ?? process.env.LOCALIZE_PYTHON ?? "python"

export default tool({
  description:
    "Reproduce a bug and find the exact source location to fix, in ONE call. Pass " +
    "the failing Python code from the issue (the call/expression that errors). The " +
    "tool runs it in this repo's environment, catches the traceback, and returns the " +
    "exception plus the recursion/error cycle — the repo files+lines that repeat — " +
    "each with a source window. Use this FIRST instead of grepping/reading to guess " +
    "the location; then edit the file it points to. Far faster and more reliable than " +
    "manual search on this model.",
  args: {
    code: tool.schema
      .string()
      .describe(
        "Python that triggers the failure, e.g. " +
          "`from sympy import sympify; sympify('cosh(acos(-i + acosh(-g + i)))').is_zero`. " +
          "No code fences.",
      ),
  },
  async execute(args) {
    const { code } = args
    if (!code || !code.trim()) throw new Error("localize: empty code")
    if (!HELPER) throw new Error("localize: LOCALIZE_HELPER not set (tool not provisioned)")

    const tmp = `${process.env.TMPDIR ?? "/tmp"}/localize_${Date.now()}_${Math.floor(
      Math.random() * 1e6,
    )}.py`
    await Bun.write(tmp, code)

    const proc = Bun.spawn({
      cmd: [PY, HELPER, tmp, "--ctx", "4", "--max-frames", "8"],
      cwd: process.cwd(),
      stdout: "pipe",
      stderr: "pipe",
    })
    const stdout = await new Response(proc.stdout).text()
    await proc.exited
    try {
      await Bun.file(tmp).unlink?.()
    } catch {
      /* best-effort temp cleanup */
    }

    const line = stdout
      .split("\n")
      .reverse()
      .find((l) => l.startsWith("LOCALIZE_JSON:"))
    if (!line) throw new Error(`localize: no result from helper (stdout: ${stdout.slice(0, 200)})`)
    let o: any
    try {
      o = JSON.parse(line.slice("LOCALIZE_JSON:".length))
    } catch {
      throw new Error(`localize: bad helper output: ${line.slice(0, 200)}`)
    }

    if (o.ok) {
      return `The snippet ran with NO error (${o.note ?? "no exception"}). If you expected a crash, adjust the reproducing code.`
    }
    if (o.kind === "snippet-syntax-error") {
      return `Your reproducing code has a syntax error: ${o.exc}. Fix the snippet and call localize again.`
    }
    if (o.kind === "no-repo-frames") {
      return `Ran, but the error was ${o.exc} — ${o.hint}`
    }

    const lines: string[] = []
    lines.push(`Reproduced — ${o.exc}`)
    lines.push(
      `Error/recursion cycle: ${o.n_distinct_frames} distinct repo frames repeat ` +
        `(stack ${o.n_repo_frames} deep).` +
        (o.driver ? ` Candidate fix site (verify against the windows below): ${o.driver}.` : ""),
    )
    lines.push(
      "The fix is in ONE of the repeating frames below — usually the domain-specific " +
        "one, NOT a generic caching/dispatch frame. Edit there:",
    )
    for (const f of (o.frames ?? []).slice(0, 5)) {
      lines.push(`\n  ×${f.repeat}  ${f.file}:${f.line}  in ${f.func}`)
      for (const s of f.src ?? []) {
        const mark = s.n === f.line ? ">>" : "  "
        lines.push(`     ${mark} ${String(s.n).padStart(4)} | ${s.t}`)
      }
    }
    return lines.join("\n")
  },
})
