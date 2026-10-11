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
import { permissionRules, spending, toJob } from "../src/lib/symphony/jobs"
import { placeForProvider } from "../src/lib/symphony/places"
import { redactContactInfo, restoreRedactions, routeTask } from "../src/lib/symphony/routing"
import { appForWorkflow, appPermissionRules, appsMentioned, describeAppTool } from "../src/lib/symphony/apps"
import { formatSaved, ratesFromProviders, summarizeSavings, TYPICAL_RATES, withSavings, type PricedProvider } from "../src/lib/symphony/savings"
import { customCategory, normalizeSettings } from "../src/lib/symphony/settings"

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

// ---------- Symphony routing, settings, jobs ----------

const base = normalizeSettings({})

check("routing: quick and private jobs stay on the Symphony", () => {
  assert.deepEqual(pick(routeTask("Write a thank-you note for the bakery window", base)), ["device", "it's a quick job", false])
  const catering = routeTask("Draft replies to the catering inquiries in my inbox", base)
  assert.deepEqual(pick(catering), ["device", "it includes customer contact info", false])
  assert.deepEqual(routeTask("Reply to jo@example.com about Saturday", base).privateHits, ["Customer contact info"])
  assert.deepEqual(routeTask("Call (803) 555-0142 back", base).privateHits, ["Customer contact info"])
})

check("routing: big private jobs go to the private cloud, big shareable ones to the open cloud", () => {
  assert.deepEqual(pick(routeTask("Analyze last year's payroll and find overtime trends", base)), [
    "private",
    "it's a big job that includes staff and payroll",
    false,
  ])
  assert.deepEqual(pick(routeTask("Write a report on fall baking trends for our blog", base)), [
    "open",
    "it's a big job with nothing private in it",
    false,
  ])
})

check("routing: web jobs need the open cloud and ask first when private info is involved", () => {
  assert.deepEqual(pick(routeTask("Look up what other bakeries online charge for cupcakes", base)), ["open", "it needs to search the web", false])
  const mixed = routeTask("Compare our customer comments with reviews of the bakeries nearby", base)
  assert.deepEqual(pick(mixed), ["open", "it needs to search the web", true])
})

check("routing: respects switched-off places and the open-cloud rule", () => {
  const noOpen = normalizeSettings({ places: { open: { enabled: false } } })
  assert.equal(routeTask("Write a report on fall baking trends", noOpen).place, "private")
  const never = normalizeSettings({ places: { open: { rule: "never" } } })
  const r = routeTask("Compare our customer comments with reviews of other bakeries nearby", never)
  assert.equal(r.place, "private")
  assert.equal(r.needsConsent, false)
  const noPrivate = normalizeSettings({ places: { private: { enabled: false } } })
  assert.equal(routeTask("Analyze last year's payroll", noPrivate).place, "device")
  const always = normalizeSettings({ places: { open: { rule: "ask-always" } } })
  assert.equal(routeTask("Write a report on fall baking trends", always).needsConsent, true)
  const off = normalizeSettings({ keepPrivate: base.keepPrivate.map((c) => ({ ...c, on: false })) })
  assert.equal(routeTask("Draft replies to the catering inquiries", off).place, "device")
  assert.deepEqual(routeTask("Draft replies to the catering inquiries", off).privateHits, [])
})

check("routing: custom keep-private items count", () => {
  const custom = normalizeSettings({ keepPrivate: [...base.keepPrivate, customCategory("Wedding cakes")] })
  assert.deepEqual(routeTask("Price list for wedding cakes", custom).privateHits, ["Wedding cakes"])
  assert.deepEqual(routeTask("Quote for a wedding cake", custom).privateHits, ["Wedding cakes"])
  assert.deepEqual(customCategory("Glass").keywords, ["glass"])
})

check("redaction removes emails and phone numbers and restores them for display", () => {
  const { text, redactions } = redactContactInfo("Email jo@example.com or call 803-555-0142, cc sam@shop.co")
  assert.equal(text, "Email [email 1] or call [phone 1], cc [email 2]")
  assert.equal(restoreRedactions("I wrote to [email 1] and [email 2]", redactions), "I wrote to jo@example.com and sam@shop.co")
})

check("settings: defaults fill gaps and bad values are cleaned", () => {
  const s = normalizeSettings({ spending: { limit: -5, atLimit: "explode" }, places: { open: { rule: "sometimes" } }, checkFirst: "yes" })
  assert.equal(s.spending.limit, 40)
  assert.equal(s.spending.atLimit, "pause")
  assert.equal(s.places.open.rule, "ask-if-private")
  assert.equal(s.checkFirst, true)
  assert.equal(s.keepPrivate.length, 6)
})

check("jobs: status, place and monthly spending", () => {
  const session = (over: Record<string, unknown>) => ({ id: "ses_a", title: "A", cost: 0.5, time: { created: Date.UTC(2026, 9, 3), updated: Date.UTC(2026, 9, 3) }, ...over })
  assert.equal(toJob(session({}), "busy", 0, base).status, "working")
  assert.equal(toJob(session({}), "busy", 1, base).status, "needs-ok")
  assert.equal(toJob(session({ revert: { messageID: "msg_1" } }), undefined, 0, base).status, "undone")
  assert.equal(toJob(session({}), "idle", 0, base).status, "done")
  assert.equal(toJob(session({ metadata: { symphony: { place: "private", reason: "x", checkFirst: true } } }), "idle", 0, base).place, "private")
  assert.equal(toJob(session({ model: { id: "m", providerID: "amazon-bedrock" } }), "idle", 0, base).place, "private")
  assert.equal(toJob(session({ model: { id: "m", providerID: "ollama" } }), "idle", 0, base).place, "device")
  assert.equal(placeForProvider("mock", { device: "mock/mock-coder" }), "device")
  const jobs = [
    toJob(session({ id: "1", cost: 0.25, metadata: { symphony: { place: "private", reason: "", checkFirst: true } } }), "idle", 0, base),
    toJob(session({ id: "2", cost: 1.5, metadata: { symphony: { place: "open", reason: "", checkFirst: true } } }), "idle", 0, base),
    toJob(session({ id: "3", cost: 9, time: { created: Date.UTC(2026, 8, 3), updated: 0 }, metadata: { symphony: { place: "open", reason: "", checkFirst: true } } }), "idle", 0, base),
  ]
  const spent = spending(jobs, base, Date.UTC(2026, 9, 10))
  assert.equal(spent.month, "2026-10")
  assert.equal(spent.total, 1.75)
  assert.deepEqual(spent.byPlace, { device: 0, private: 0.25, open: 1.5 })
})

check("savings: priced from tokens against the open cloud, never invented", () => {
  const providers: PricedProvider[] = [
    { id: "anthropic", models: { "big-model": { id: "big-model", name: "Big model", cost: { input: 5, output: 25, cache: { read: 0.5, write: 6.25 } } } } },
    { id: "mock", models: { "mock-coder": { id: "mock-coder", name: "Mock coder" } } },
  ]
  const rates = ratesFromProviders("anthropic/big-model", providers)!
  assert.equal(rates.label, "Big model")
  assert.equal(ratesFromProviders("mock/mock-coder", providers), null, "unpriced models fall back")
  assert.equal(ratesFromProviders("nope", providers), null)
  const at = (place: string, cost: number, tokens: unknown, created = Date.UTC(2026, 9, 3)) =>
    withSavings(
      toJob(
        { id: place + cost, cost, tokens, time: { created, updated: created }, metadata: { symphony: { place, reason: "", checkFirst: true } } },
        "idle",
        0,
        base,
      ),
      rates,
    )
  // 1M input at $5 + 100k output at $25 + 200k cache reads at $0.50 = 5 + 2.5 + 0.1
  const device = at("device", 0, { input: 1_000_000, output: 80_000, reasoning: 20_000, cache: { read: 200_000, write: 0 } })
  assert.equal(device.wouldHaveCost?.toFixed(2), "7.60")
  assert.equal(device.saved?.toFixed(2), "7.60")
  const priv = at("private", 1.2, { input: 400_000, output: 0, reasoning: 0, cache: { read: 0, write: 0 } })
  assert.equal(priv.saved?.toFixed(2), "0.80", "private saves the difference")
  const open = at("open", 3, { input: 1_000_000, output: 0, reasoning: 0, cache: { read: 0, write: 0 } })
  assert.equal(open.saved, 0, "open-cloud jobs save nothing")
  const old = at("device", 0, { input: 200_000, output: 0, reasoning: 0, cache: { read: 0, write: 0 } }, Date.UTC(2026, 8, 3))
  const noTokens = at("device", 0, undefined)
  assert.equal(noTokens.saved, undefined)
  const sum = summarizeSavings([device, priv, open, old, noTokens], rates, Date.UTC(2026, 9, 10))
  assert.equal(sum.jobs, 2)
  assert.equal(sum.saved.toFixed(2), "8.40")
  assert.equal(sum.wouldHaveCost.toFixed(2), "9.60")
  assert.equal(sum.actual.toFixed(2), "1.20")
  assert.equal(sum.allTime.toFixed(2), "9.40")
  assert.equal(sum.byPlace.device.toFixed(2), "7.60")
  assert.equal(TYPICAL_RATES.typical, true)
  assert.equal(formatSaved(0.004), "under 1¢")
  assert.equal(formatSaved(-0.4), "−$0.40")
  assert.equal(normalizeSettings({}).savings.compareWith, "open")
})

check("connected apps: matching, routing and the n8n guard", () => {
  const wf = (name: string, tags: string[] = [], description = "") => ({ name, tags, description })
  assert.equal(appForWorkflow(wf("Square: today's sales"))?.id, "square")
  assert.equal(appForWorkflow(wf("Daily totals", ["Square"]))?.id, "square", "a tag naming the app wins")
  assert.equal(appForWorkflow(wf("Book a tasting", [], "Adds an event to Google Calendar"))?.id, "google-calendar")
  assert.equal(appForWorkflow(wf("Squared numbers")), null, "whole words only")
  assert.deepEqual(appsMentioned("What did we sell on Square today?", ["square"]).map((a) => a.id), ["square"])
  assert.deepEqual(appsMentioned("What did we sell on Square today?", []), [], "only apps with actions count")

  // A request that uses Square touches customers and money, so it stays private.
  const s = normalizeSettings({})
  const d = routeTask("What did we sell on Square today?", s, ["square"])
  assert.equal(d.place, "device")
  assert.deepEqual(d.apps, ["Square"])
  assert.equal(d.reason, "it reads customer contact info from Square")
  assert.equal(routeTask("Email jo@example.com our Square totals", s, ["square"]).reason, "it includes customer contact info")
  assert.deepEqual(d.privateHits, ["Customer contact info", "Bank and card details"])
  assert.equal(routeTask("What did we sell on Square today?", s, []).apps, undefined)
  const off = normalizeSettings({ apps: { enabled: false } })
  assert.equal(routeTask("What did we sell on Square today?", off, ["square"]).apps, undefined, "apps switched off")
  // Web + Square goes to the open cloud only with consent.
  assert.equal(routeTask("Compare our Square prices with bakeries online", s, ["square"]).needsConsent, true)

  // n8n tools: everything hidden unless allowed; running a workflow asks.
  const evaluate = (rules: { permission: string; action: string }[], tool: string) => {
    const match = (pat: string) => new RegExp(`^${pat.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")}$`).test(tool)
    return rules.filter((r) => match(r.permission)).at(-1)?.action ?? "ask"
  }
  const on = appPermissionRules({ enabled: true, ask: true })
  assert.equal(evaluate(on, "n8n_search_workflows"), "allow")
  assert.equal(evaluate(on, "n8n_get_execution"), "allow")
  assert.equal(evaluate(on, "n8n_execute_workflow"), "ask")
  assert.equal(evaluate(on, "n8n_delete_workflow"), "deny")
  assert.equal(evaluate(on, "n8n_list_credentials"), "deny")
  assert.equal(evaluate(on, "n8n_install_community_node"), "deny")
  assert.equal(evaluate(on, "bash"), "ask", "other tools untouched")
  assert.equal(evaluate(appPermissionRules({ enabled: true, ask: false }), "n8n_execute_workflow"), "allow")
  assert.equal(evaluate(appPermissionRules({ enabled: false, ask: true }), "n8n_search_workflows"), "deny")
  assert.equal(describeAppTool("n8n_execute_workflow", "Square: today's sales"), "Run “Square: today's sales”")
  assert.equal(describeAppTool("bash"), null)
})

check("check-with-me-first maps to opencode permission rules", () => {
  assert.deepEqual(
    permissionRules(true).map((r) => `${r.permission}:${r.action}`),
    ["edit:ask", "bash:ask", "webfetch:ask"],
  )
  assert.ok(permissionRules(false).every((r) => r.action === "allow"))
})

function pick(r: { place: string; reason: string; needsConsent: boolean }) {
  return [r.place, r.reason, r.needsConsent]
}

console.log(`\n${passed} checks passed`)
