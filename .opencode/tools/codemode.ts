/**
 * Custom `codemode` tool — TODO 21.4 (productionise the 21.3 round-trip win).
 *
 * The 21.3 A/B (`scripts/codemode_ab.py`) showed that on the local Gemma-4 stack,
 * collapsing N tool calls into ONE sandboxed Python code block is ~5x faster
 * (40s vs 245s/task), uses ~91% fewer tokens, and — critically — AVOIDS the
 * no-tool-stop / tool-churn non-termination that fails the flat one-call-per-turn
 * loop on 4/6 multi-step tasks. This tool brings that into real opencode: the model
 * emits one Python program that calls real filesystem host-tools and chains many
 * operations in a single tool call, returning only the consolidated result.
 *
 * Execution is delegated to `scripts/codemode_exec.py`, which runs the code in the
 * SAME restricted sandbox validated by `codegen_probe` (the model writes Python;
 * we proved Gemma-4-E4B does this at pass@1 1.0 on both task tiers). Read-only host
 * tools by default; bash/write are opt-in via env. Paths cannot escape the project.
 *
 * opencode keys tools by filename, so this adds a `codemode` tool to the toolset.
 *   docs: https://opencode.ai/docs/custom-tools/
 */
import { tool } from "@opencode-ai/plugin"

// Executor path: env override (set by the A/B harness when this tool is installed into
// an isolated config dir) else repo-relative to this file. Host-tools are bound to the
// PROJECT opencode is working in (cwd / --dir), overridable via CODEMODE_ROOT.
const REPO_ROOT = `${import.meta.dir}/../..`
const EXEC = process.env.CODEMODE_EXEC ?? `${REPO_ROOT}/scripts/codemode_exec.py`
const ROOT = process.env.CODEMODE_ROOT ?? process.cwd()
const ALLOW_BASH = process.env.CODEMODE_ALLOW_BASH === "1"
const ALLOW_WRITE = process.env.CODEMODE_ALLOW_WRITE === "1"
const ENGINE = process.env.CODEMODE_ENGINE ?? "exec" // "exec" | "monty"

async function pythonBin(): Promise<string> {
  if (process.env.CODEMODE_PYTHON) return process.env.CODEMODE_PYTHON
  const venv = `${REPO_ROOT}/.venv/bin/python`
  return (await Bun.file(venv).exists()) ? venv : "python3"
}

export default tool({
  description:
    "Run ONE Python program that calls filesystem host-tools to do MANY operations " +
    "at once, returning only the final consolidated result. Use this INSTEAD of many " +
    "separate read/grep/list calls whenever you need to gather, scan, count, or " +
    "aggregate across multiple files — it is far faster on this model because it avoids " +
    "a round-trip per operation.\n\n" +
    "Host-tools available inside your code (already defined, just call them):\n" +
    "  read_file(path) -> str\n" +
    "  read_lines(path, start=1, end=None) -> list[str]   # 1-indexed, inclusive; end=None means to EOF\n" +
    "  list_files(dir='.') -> list[str]\n" +
    "  glob(pattern) -> list[str]   # supports ** , e.g. glob('src/**/*.py')\n" +
    "  grep(pattern, path='.') -> list[str]   # 'relpath:lineno:line', Python regex\n" +
    "Rules: write plain Python (loops, conditionals, comprehensions are fine). Do NOT " +
    "import anything or redefine the host-tools. Assign your final answer to a variable " +
    "named `result`. Return small data — filter/aggregate in code, don't dump whole files.",
  args: {
    code: tool.schema
      .string()
      .describe(
        "A Python program (no fences) that calls the host-tools and assigns the answer " +
          "to `result`. Example: `result = sum(len(read_file(p).splitlines()) " +
          "for p in glob('src/**/*.py'))`",
      ),
  },
  async execute(args) {
    const { code } = args
    if (!code || !code.trim()) throw new Error("codemode: empty code")

    const flags = ["--root", ROOT, "--engine", ENGINE]
    if (ALLOW_BASH) flags.push("--allow-bash")
    if (ALLOW_WRITE) flags.push("--allow-write")

    const proc = Bun.spawn({
      cmd: [await pythonBin(), EXEC, ...flags],
      stdin: new Blob([code]),
      stdout: "pipe",
      stderr: "pipe",
    })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    const exitCode = await proc.exited
    if (exitCode !== 0) {
      const msg = stderr.trim().split("\n").slice(-1)[0] || `exit ${exitCode}`
      throw new Error(`codemode executor failed: ${msg}`)
    }

    let env: any
    try {
      env = JSON.parse(stdout.trim().split("\n").slice(-1)[0])
    } catch {
      throw new Error(`codemode: bad executor output: ${stdout.slice(0, 200)}`)
    }
    if (!env.ok) {
      // Surface the sandbox error so the model can fix its code and retry.
      throw new Error(`codemode error (${env.n_calls} host-calls ran): ${env.error}`)
    }
    return (
      `result (${env.n_calls} host-tool calls in one pass):\n` +
      env.result_repr
    )
  },
})
