// Line diffs for the Changes panel. opencode 1.18 sends a unified `patch`;
// older servers sent full `before`/`after` text, which goes through a simple
// LCS diff. Both produce the same rows.
import type { FileDiff, MessageInfo } from "./types"

/**
 * opencode 1.18 records diffs per turn, on each user message's
 * `summary.diffs`; the session-wide diff endpoint returns []. Combine the
 * turns into one entry per file, oldest turn first.
 */
export function aggregateTurnDiffs(infos: MessageInfo[]): FileDiff[] {
  const byFile = new Map<string, FileDiff>()
  const turns = infos
    .filter((m) => m.role === "user" && m.summary?.diffs?.length)
    .sort((a, b) => (a.time?.created ?? 0) - (b.time?.created ?? 0) || (a.id < b.id ? -1 : 1))
  for (const turn of turns) {
    for (const d of turn.summary!.diffs!) {
      const key = d.file ?? "(unknown file)"
      const prev = byFile.get(key)
      if (!prev) {
        byFile.set(key, { ...d })
        continue
      }
      byFile.set(key, {
        file: d.file,
        patch: [prev.patch, d.patch].filter(Boolean).join("\n") || undefined,
        before: prev.before,
        after: d.after,
        additions: (prev.additions || 0) + (d.additions || 0),
        deletions: (prev.deletions || 0) + (d.deletions || 0),
        status: prev.status === "added" && d.status !== "deleted" ? "added" : d.status ?? prev.status,
      })
    }
  }
  return [...byFile.values()]
}

export type DiffRow =
  | { kind: "same" | "add" | "del"; text: string; oldNo?: number; newNo?: number }
  | { kind: "gap"; count?: number; header?: string }

export type DiffResult = { rows: DiffRow[] } | { tooLarge: true }

const MAX_LINES = 4000
const MAX_CELLS = 4_000_000
const MAX_PATCH_CHARS = 400_000

function splitLines(text: string): string[] {
  if (!text) return []
  const lines = text.split("\n")
  if (lines[lines.length - 1] === "") lines.pop()
  return lines
}

/** Longest-common-subsequence line diff. Returns null when the input is too big. */
export function lineDiff(before: string, after: string): DiffRow[] | null {
  const a = splitLines(before)
  const b = splitLines(after)
  if (a.length > MAX_LINES || b.length > MAX_LINES) return null

  // Trim the common prefix and suffix so the DP table only covers the changed middle.
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start++
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--
    endB--
  }
  const n = endA - start
  const m = endB - start
  if (n * m > MAX_CELLS) return null

  const table: Uint32Array[] = []
  for (let i = 0; i <= n; i++) table.push(new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = a[start + i] === b[start + j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }

  const rows: DiffRow[] = []
  for (let k = 0; k < start; k++) rows.push({ kind: "same", text: a[k], oldNo: k + 1, newNo: k + 1 })
  let i = 0
  let j = 0
  while (i < n || j < m) {
    if (i < n && j < m && a[start + i] === b[start + j]) {
      rows.push({ kind: "same", text: a[start + i], oldNo: start + i + 1, newNo: start + j + 1 })
      i++
      j++
    } else if (i < n && (j >= m || table[i + 1][j] >= table[i][j + 1])) {
      rows.push({ kind: "del", text: a[start + i], oldNo: start + i + 1 })
      i++
    } else {
      rows.push({ kind: "add", text: b[start + j], newNo: start + j + 1 })
      j++
    }
  }
  for (let k = 0; k < a.length - endA; k++) {
    rows.push({ kind: "same", text: a[endA + k], oldNo: endA + k + 1, newNo: endB + k + 1 })
  }
  return rows
}

/** Collapse runs of unchanged lines, keeping `context` lines around each change. */
export function collapse(rows: DiffRow[], context = 3): DiffRow[] {
  const changed = rows.map((r) => r.kind === "add" || r.kind === "del")
  const keep = rows.map(() => false)
  changed.forEach((isChange, idx) => {
    if (!isChange) return
    for (let k = Math.max(0, idx - context); k <= Math.min(rows.length - 1, idx + context); k++) keep[k] = true
  })
  const out: DiffRow[] = []
  let hidden = 0
  rows.forEach((row, idx) => {
    if (keep[idx]) {
      if (hidden) out.push({ kind: "gap", count: hidden })
      hidden = 0
      out.push(row)
    } else {
      hidden++
    }
  })
  if (hidden && out.length) out.push({ kind: "gap", count: hidden })
  return out
}

/** Parse a unified diff (as returned by opencode's /session/:id/diff). */
export function parsePatch(patch: string): DiffRow[] | null {
  if (patch.length > MAX_PATCH_CHARS) return null
  const rows: DiffRow[] = []
  let oldNo = 0
  let newNo = 0
  let inHunk = false
  for (const line of patch.split("\n")) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line)
    if (hunk) {
      oldNo = Number(hunk[1])
      newNo = Number(hunk[2])
      inHunk = true
      rows.push({ kind: "gap", header: hunk[3].trim() || undefined })
      continue
    }
    if (!inHunk) continue
    if (line.startsWith("+")) rows.push({ kind: "add", text: line.slice(1), newNo: newNo++ })
    else if (line.startsWith("-")) rows.push({ kind: "del", text: line.slice(1), oldNo: oldNo++ })
    else if (line.startsWith(" ")) rows.push({ kind: "same", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ })
    else if (line.startsWith("diff ") || line.startsWith("Index: ") || line.startsWith("===")) inHunk = false
  }
  // The first hunk marker at the very top adds nothing.
  if (rows[0]?.kind === "gap" && !rows[0].header) rows.shift()
  return rows
}

export function diffRows(file: FileDiff): DiffResult {
  if (typeof file.patch === "string" && file.patch) {
    const rows = parsePatch(file.patch)
    return rows ? { rows } : { tooLarge: true }
  }
  if (typeof file.before === "string" || typeof file.after === "string") {
    const rows = lineDiff(file.before ?? "", file.after ?? "")
    return rows ? { rows: collapse(rows) } : { tooLarge: true }
  }
  return { rows: [] }
}
