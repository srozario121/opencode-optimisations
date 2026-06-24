/**
 * Custom `edit` tool — shadows opencode's built-in edit with a FORGIVING matcher
 * (item 16 L3b). On the local Gemma-4 harness two real `oldString` match-failure
 * modes were observed (4 `edit:error`s across the trace runs, all "Could not find
 * oldString … must match exactly"):
 *
 *   1. **Read-gutter leak.** The custom read tool renders each line as
 *      `${n} | ${line}` (../tools/read.ts), and the model pastes that back —
 *      e.g. oldString = '32 |     "conjugate": [...]'. The "32 | " gutter is not
 *      in the file, so an exact match never finds it.
 *   2. **Whitespace/indentation drift.** oldString matches the file except for
 *      leading whitespace (the model re-typed the indentation slightly wrong).
 *
 * Strategy (each step only runs if the previous found nothing, so behaviour for
 * edits that already matched exactly is UNCHANGED):
 *   (a) exact substring match — the built-in behaviour;
 *   (b) strip the `N | ` gutter from oldString/newString, retry exact;
 *   (c) whitespace-flexible: find the UNIQUE block of file lines whose trimmed
 *       content equals the trimmed oldString lines, and replace it with newString
 *       re-indented to the file's actual indentation. Never guesses on an
 *       ambiguous (>1) or zero match.
 *
 * opencode keys tools by filename; a custom tool sharing a built-in name takes
 * precedence, so this file replaces the native edit tool.
 *   docs: https://opencode.ai/docs/custom-tools/
 */
import { tool } from "@opencode-ai/plugin"

// read.ts line-number prefix, e.g. "  32 | " — number, optional ws, pipe, space.
const GUTTER = /^[ \t]*\d+[ \t]*\|[ \t]?/

function isSecretFile(filePath: string): boolean {
  const base = (filePath.split("/").pop() ?? filePath).toLowerCase()
  if (/\.(example|sample|template)$/.test(base)) return false
  return /^\.env(\..+)?$/.test(base)
}

// Strip the read-tool gutter from every line — but only when (nearly) ALL
// non-empty lines carry it, so we never mangle legitimate content that happens
// to start with "<digits> |".
function deGutter(s: string): string {
  const lines = s.split("\n")
  const nonEmpty = lines.filter((l) => l.trim().length > 0)
  if (nonEmpty.length === 0) return s
  const guttered = nonEmpty.filter((l) => GUTTER.test(l)).length
  if (guttered < nonEmpty.length) return s
  return lines.map((l) => l.replace(GUTTER, "")).join("\n")
}

function countOccurrences(hay: string, needle: string): number {
  if (!needle) return 0
  let n = 0
  let i = hay.indexOf(needle)
  while (i !== -1) {
    n++
    i = hay.indexOf(needle, i + needle.length)
  }
  return n
}

const leadingWS = (l: string): string => (l.match(/^[ \t]*/)?.[0] ?? "")

/**
 * Whitespace-flexible replace: locate the unique window of `content` lines whose
 * trimmed text matches `oldLines` trimmed, then splice in `newLines` re-indented
 * by the (fileIndent − oldIndent) delta of the first matched line. Returns the
 * new content, or null if there is no unique match.
 */
function flexibleReplace(
  content: string,
  oldStr: string,
  newStr: string,
): string | null {
  const fileLines = content.split("\n")
  const oldLines = oldStr.split("\n")
  const newLines = newStr.split("\n")
  const trimmedOld = oldLines.map((l) => l.trim())
  // Find every window whose trimmed lines equal trimmedOld.
  const hits: number[] = []
  for (let i = 0; i + oldLines.length <= fileLines.length; i++) {
    let ok = true
    for (let j = 0; j < oldLines.length; j++) {
      if (fileLines[i + j].trim() !== trimmedOld[j]) {
        ok = false
        break
      }
    }
    if (ok) hits.push(i)
  }
  if (hits.length !== 1) return null // zero or ambiguous — refuse to guess
  const at = hits[0]
  // Re-indent newString by the indentation delta of the matched block's 1st line.
  const fileIndent = leadingWS(fileLines[at])
  const oldIndent = leadingWS(oldLines[0])
  const reindented = newLines.map((l) => {
    if (l.trim().length === 0) return l
    if (oldIndent && l.startsWith(oldIndent)) return fileIndent + l.slice(oldIndent.length)
    return fileIndent + l.replace(/^[ \t]*/, "")
  })
  return [
    ...fileLines.slice(0, at),
    ...reindented,
    ...fileLines.slice(at + oldLines.length),
  ].join("\n")
}

export default tool({
  description:
    "Edit a file by replacing oldString with newString. Matches exactly first; " +
    "if that fails it tolerates a leading line-number gutter (from read output) " +
    "and whitespace/indentation differences, re-indenting to the file. Set " +
    "replaceAll to replace every occurrence; otherwise oldString must be unique.",
  args: {
    filePath: tool.schema.string().describe("Absolute path to the file to edit"),
    oldString: tool.schema
      .string()
      .describe("Exact text to find and replace (empty to create a new file)"),
    newString: tool.schema.string().describe("Replacement text"),
    replaceAll: tool.schema
      .boolean()
      .optional()
      .describe("Replace all occurrences (default: false — oldString must be unique)"),
  },
  async execute(args) {
    const { filePath, oldString, newString, replaceAll } = args
    if (isSecretFile(filePath)) {
      throw new Error(`Refusing to edit potential secret file: ${filePath}`)
    }

    // Empty oldString = create a new file (never overwrite an existing one).
    if (oldString === "") {
      if (await Bun.file(filePath).exists()) {
        throw new Error(
          `oldString is empty but ${filePath} exists — refusing to overwrite. ` +
            `Provide the text to replace.`,
        )
      }
      await Bun.write(filePath, newString)
      return `Created ${filePath}.`
    }

    let content: string
    try {
      content = await Bun.file(filePath).text()
    } catch {
      throw new Error(`File not found or unreadable: ${filePath}`)
    }

    // Returns the rewritten file content + a note, or null if `old` isn't found.
    // Throws on a non-unique match when replaceAll is off (built-in semantics).
    const tryReplace = (
      old: string,
      neu: string,
      how: string,
    ): { note: string; out: string } | null => {
      const occ = countOccurrences(content, old)
      if (occ === 0) return null
      if (occ > 1 && !replaceAll) {
        throw new Error(
          `oldString is not unique (${occ} matches) — pass replaceAll or add ` +
            `surrounding context.`,
        )
      }
      const out = replaceAll
        ? content.split(old).join(neu)
        : content.replace(old, neu)
      return { note: how + (occ > 1 ? ` (${occ} occurrences)` : ""), out }
    }

    // (a) exact — unchanged built-in behaviour.
    let res = tryReplace(oldString, newString, "Edit applied successfully.")

    // (b) de-gutter (read line-number prefix leaked into oldString/newString).
    if (!res) {
      const dOld = deGutter(oldString)
      if (dOld !== oldString) {
        res = tryReplace(
          dOld,
          deGutter(newString),
          "Edit applied successfully (stripped read gutter).",
        )
      }
    }

    // (c) whitespace-flexible unique-window match (de-guttered).
    if (!res) {
      const flex = flexibleReplace(content, deGutter(oldString), deGutter(newString))
      if (flex !== null) {
        res = { note: "Edit applied successfully (whitespace-tolerant match).", out: flex }
      }
    }

    if (!res) {
      throw new Error(
        "Could not find oldString in the file (tried exact, gutter-stripped, and " +
          "whitespace-tolerant matching). Re-read the file and copy the exact text " +
          "WITHOUT the leading line numbers.",
      )
    }
    await Bun.write(filePath, res.out)
    return res.note
  },
})
