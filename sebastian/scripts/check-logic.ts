// Unit checks for the Coding agent's pure logic. Run with: npm run check:logic
import assert from "node:assert/strict"
import { collapse, diffRows, lineDiff, parsePatch } from "../src/lib/opencode/diff"
import {
  initialAgentState,
  mergeSnapshot,
  normalizePermission,
  reduceEvent,
  sortedMessages,
  sortedSessions,
  unwrapEvent,
  type AgentState,
} from "../src/lib/opencode/state"
import type { MessageInfo, OcEvent, Part } from "../src/lib/opencode/types"

let passed = 0
function check(name: string, fn: () => void) {
  fn()
  passed++
  console.log(`ok - ${name}`)
}

const S = "ses_1"
const user: MessageInfo = { id: "msg_a", sessionID: S, role: "user", time: { created: 100 } }
const agent: MessageInfo = { id: "msg_b", sessionID: S, role: "assistant", time: { created: 200 } }
const textPart = (id: string, messageID: string, text: string): Part => ({ id, messageID, sessionID: S, type: "text", text })
const toolPart = (id: string, status: "pending" | "running" | "completed"): Part => ({
  id,
  messageID: "msg_b",
  sessionID: S,
  type: "tool",
  tool: "bash",
  state: { status, input: { command: "ls" } },
})

function run(events: OcEvent[], active = S, start: AgentState = initialAgentState) {
  return events.reduce((s, e) => reduceEvent(s, e, active).state, start)
}

check("snapshot merge keeps parts that streamed in while history was loading", () => {
  // The first tool call arrives over SSE before the history request returns.
  const live = run([
    { type: "message.updated", properties: { sessionID: S, info: agent } },
    { type: "message.part.updated", properties: { sessionID: S, part: toolPart("prt_2", "running") } },
  ]).messages
  // The snapshot was taken just before that tool call existed.
  const snapshot = [
    { info: user, parts: [textPart("prt_0", "msg_a", "list files")] },
    { info: agent, parts: [textPart("prt_1", "msg_b", "Sure")] },
  ]
  const merged = sortedMessages(mergeSnapshot(live, snapshot))
  assert.equal(merged.length, 2)
  assert.deepEqual(
    merged[1].parts.map((p) => p.id),
    ["prt_1", "prt_2"],
  )
  assert.equal(merged[1].parts[1].state?.status, "running")
})

check("live parts win, but a further-along snapshot copy is not thrown away", () => {
  const live = { msg_b: { info: agent, parts: { prt_2: toolPart("prt_2", "running"), prt_1: textPart("prt_1", "msg_b", "He") } } }
  const merged = mergeSnapshot(live, [{ info: agent, parts: [toolPart("prt_2", "completed"), textPart("prt_1", "msg_b", "Hello")] }])
  assert.equal(merged.msg_b.parts.prt_2.state?.status, "completed")
  assert.equal(merged.msg_b.parts.prt_1.text, "Hello")
  const merged2 = mergeSnapshot(
    { msg_b: { info: agent, parts: { prt_2: toolPart("prt_2", "completed") } } },
    [{ info: agent, parts: [toolPart("prt_2", "running")] }],
  )
  assert.equal(merged2.msg_b.parts.prt_2.state?.status, "completed")
})

check("messages sort by created time then id; parts by id", () => {
  const map = {
    msg_b: { info: agent, parts: { prt_9: textPart("prt_9", "msg_b", "b"), prt_3: textPart("prt_3", "msg_b", "a") } },
    msg_a: { info: user, parts: {} },
    msg_c: { info: { ...agent, id: "msg_c", time: { created: 200 } }, parts: {} },
    msg_x: { parts: {} },
  }
  const sorted = sortedMessages(map)
  assert.deepEqual(
    sorted.map((m) => m.info.id),
    ["msg_a", "msg_b", "msg_c"],
  )
  assert.deepEqual(
    sorted[1].parts.map((p) => p.id),
    ["prt_3", "prt_9"],
  )
})

check("part arriving before its message creates the entry", () => {
  const state = run([{ type: "message.part.updated", properties: { sessionID: S, part: textPart("prt_1", "msg_b", "hi") } }])
  assert.ok(state.messages.msg_b.parts.prt_1)
  assert.equal(sortedMessages(state.messages).length, 0, "hidden until message.updated gives it info")
  const later = run([{ type: "message.updated", properties: { sessionID: S, info: agent } }], S, state)
  assert.equal(sortedMessages(later.messages)[0].parts[0].text, "hi")
})

check("message.part.delta streams text into a part", () => {
  const state = run([
    { type: "message.updated", properties: { sessionID: S, info: agent } },
    { type: "message.part.updated", properties: { sessionID: S, part: textPart("prt_1", "msg_b", "") } },
    { type: "message.part.delta", properties: { sessionID: S, messageID: "msg_b", partID: "prt_1", field: "text", delta: "Hel" } },
    { type: "message.part.delta", properties: { sessionID: S, messageID: "msg_b", partID: "prt_1", field: "text", delta: "lo" } },
  ])
  assert.equal(state.messages.msg_b.parts.prt_1.text, "Hello")
})

check("events for other sessions don't touch the open thread", () => {
  const state = run([
    { type: "message.updated", properties: { sessionID: "ses_other", info: { ...agent, sessionID: "ses_other" } } },
    { type: "message.part.updated", properties: { sessionID: "ses_other", part: { ...textPart("p", "m", "x"), sessionID: "ses_other" } } },
  ])
  assert.deepEqual(state.messages, {})
})

check("session list: upsert, delete, status, child sessions hidden", () => {
  const base = { id: "ses_1", title: "A", time: { created: 1, updated: 5 } }
  let state = run([
    { type: "session.created", properties: { sessionID: "ses_1", info: base } },
    { type: "session.created", properties: { sessionID: "ses_2", info: { id: "ses_2", title: "B", time: { created: 2, updated: 9 } } } },
    { type: "session.created", properties: { sessionID: "ses_3", info: { id: "ses_3", parentID: "ses_1", time: { created: 3, updated: 10 } } } },
    { type: "session.updated", properties: { sessionID: "ses_1", info: { ...base, title: "A2", time: { created: 1, updated: 20 } } } },
    { type: "session.status", properties: { sessionID: "ses_1", status: { type: "busy" } } },
  ])
  assert.deepEqual(
    sortedSessions(state.sessions).map((s) => s.title),
    ["A2", "B"],
  )
  assert.equal(state.status.ses_1, "busy")
  const idle = reduceEvent(state, { type: "session.status", properties: { sessionID: "ses_1", status: { type: "idle" } } }, "ses_1")
  assert.equal(idle.effects.refreshDiff, true)
  state = run([{ type: "session.deleted", properties: { sessionID: "ses_2", info: { id: "ses_2", time: { created: 2, updated: 9 } } } }], S, idle.state)
  assert.equal(state.sessions.ses_2, undefined)
})

check("session.idle refreshes the diff only for the open session", () => {
  assert.equal(reduceEvent(initialAgentState, { type: "session.idle", properties: { sessionID: S } }, S).effects.refreshDiff, true)
  assert.equal(reduceEvent(initialAgentState, { type: "session.idle", properties: { sessionID: "x" } }, S).effects.refreshDiff, false)
})

check("permissions: asked (new and old names) then replied", () => {
  let state = run([
    {
      type: "permission.asked",
      properties: { id: "per_1", sessionID: S, permission: "bash", patterns: ["git status"], metadata: { command: "git status" }, always: [] },
    },
    { type: "permission.updated", properties: { id: "per_2", sessionID: S, type: "edit", title: "Edit src/a.ts", metadata: {} } },
  ])
  assert.equal(state.permissions.per_1.title, "Run a shell command")
  assert.deepEqual(state.permissions.per_1.details, ["git status"])
  assert.equal(state.permissions.per_2.title, "Edit src/a.ts")
  state = run([{ type: "permission.replied", properties: { sessionID: S, requestID: "per_1", reply: "once" } }], S, state)
  state = run([{ type: "permission.replied", properties: { sessionID: S, permissionID: "per_2", response: "reject" } }], S, state)
  assert.deepEqual(state.permissions, {})
})

check("session.error shows a readable message; aborts read as Stopped", () => {
  const e = run([{ type: "session.error", properties: { sessionID: S, error: { name: "APIError", data: { message: "Rate limited" } } } }])
  assert.equal(e.sessionError, "Rate limited")
  const a = run([{ type: "session.error", properties: { sessionID: S, error: { name: "MessageAbortedError", data: {} } } }])
  assert.equal(a.sessionError, "Stopped.")
})

check("unwrapEvent handles bare and { payload } events", () => {
  assert.equal(unwrapEvent({ type: "session.idle", properties: { sessionID: S } })?.type, "session.idle")
  assert.equal(unwrapEvent({ directory: "/x", payload: { type: "session.idle", properties: {} } })?.type, "session.idle")
  assert.equal(unwrapEvent({ nope: 1 }), null)
})

check("normalizePermission falls back sensibly", () => {
  const p = normalizePermission({ id: "per_x", sessionID: S, action: "webfetch", resources: ["https://example.com"] })
  assert.equal(p.title, "Fetch a web page")
  assert.deepEqual(p.details, ["https://example.com"])
})

check("lineDiff + collapse: LCS rows with 3 lines of context", () => {
  const before = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`).join("\n")
  const after = before.replace("line 10", "line ten").replace("line 20", "line 20\nline 21")
  const rows = lineDiff(before, after)
  assert.ok(rows)
  const kinds = rows!.filter((r) => r.kind !== "same").map((r) => `${r.kind}:${"text" in r ? r.text : ""}`)
  assert.deepEqual(kinds, ["del:line 10", "add:line ten", "add:line 21"])
  const collapsed = collapse(rows!)
  assert.equal(collapsed[0].kind, "gap")
  assert.equal((collapsed[0] as { count?: number }).count, 6)
  assert.ok(collapsed.length < rows!.length)
})

check("lineDiff bails out on huge files", () => {
  const big = Array.from({ length: 5000 }, (_, i) => `x${i}`).join("\n")
  assert.equal(lineDiff(big, big + "\ny"), null)
  assert.deepEqual(diffRows({ before: big, after: big + "\ny", additions: 1, deletions: 0 }), { tooLarge: true })
})

check("parsePatch reads opencode's unified patch format", () => {
  const patch = [
    "Index: src/a.ts",
    "===================================================================",
    "--- src/a.ts",
    "+++ src/a.ts",
    "@@ -1,3 +1,3 @@",
    " keep",
    "-old",
    "+new",
    " tail",
    "@@ -40,2 +40,3 @@ function later()",
    " a",
    "+b",
    " c",
  ].join("\n")
  const rows = parsePatch(patch)!
  assert.deepEqual(
    rows.map((r) => r.kind),
    ["same", "del", "add", "same", "gap", "same", "add", "same"],
  )
  assert.equal((rows[4] as { header?: string }).header, "function later()")
  const added = rows[6] as { newNo?: number }
  assert.equal(added.newNo, 41)
  assert.deepEqual(diffRows({ patch, additions: 2, deletions: 1 }), { rows })
})

check("per-turn diffs (opencode 1.18) combine per file; empty session.diff is ignored", () => {
  const patchA = "Index: a.ts\n--- a.ts\n+++ a.ts\n@@ -1,1 +1,1 @@\n-x\n+y\n"
  const patchB = "Index: a.ts\n--- a.ts\n+++ a.ts\n@@ -5,0 +5,1 @@\n+z\n"
  const turn1 = { ...user, summary: { diffs: [{ file: "a.ts", patch: patchA, additions: 1, deletions: 1, status: "modified" as const }] } }
  const turn2 = {
    ...user,
    id: "msg_c",
    time: { created: 300 },
    summary: {
      diffs: [
        { file: "a.ts", patch: patchB, additions: 1, deletions: 0, status: "modified" as const },
        { file: "new.md", patch: "@@ -0,0 +1,1 @@\n+hi\n", additions: 1, deletions: 0, status: "added" as const },
      ],
    },
  }
  let state = run([
    { type: "session.diff", properties: { sessionID: S, diff: [] } },
    { type: "message.updated", properties: { sessionID: S, info: turn1 } },
    { type: "message.updated", properties: { sessionID: S, info: turn2 } },
  ])
  assert.deepEqual(
    state.diff!.map((d) => [d.file, d.additions, d.deletions, d.status]),
    [
      ["a.ts", 2, 1, "modified"],
      ["new.md", 1, 0, "added"],
    ],
  )
  const rows = diffRows(state.diff![0])
  assert.ok(!("tooLarge" in rows))
  assert.deepEqual(
    rows.rows.filter((r) => r.kind !== "gap" && r.kind !== "same").map((r) => ("text" in r ? r.text : "")),
    ["x", "y", "z"],
  )
  state = run([{ type: "session.diff", properties: { sessionID: S, diff: [] } }], S, state)
  assert.equal(state.diff!.length, 2, "an empty session.diff must not blank the panel")
})

console.log(`\n${passed} checks passed`)
