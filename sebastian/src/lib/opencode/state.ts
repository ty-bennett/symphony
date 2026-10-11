// Pure state logic for the Coding agent tab. No React here, so it can be unit
// tested (see scripts/check-logic.ts).
import { describeAppTool } from "@/lib/symphony/apps"
import { aggregateTurnDiffs } from "./diff"
import type {
  FileDiff,
  MessageEntry,
  MessageInfo,
  MessageMap,
  MessageWithParts,
  OcEvent,
  Part,
  Permission,
  RawPermission,
  Session,
  SessionStatus,
  Todo,
} from "./types"

export type AgentState = {
  sessions: Record<string, Session>
  status: Record<string, SessionStatus["type"]>
  /** Messages for the active session only. */
  messages: MessageMap
  permissions: Record<string, Permission>
  diff: FileDiff[] | null
  todos: Todo[]
  /** Last session error for the active session, shown in the banner. */
  sessionError: string | null
}

export const initialAgentState: AgentState = {
  sessions: {},
  status: {},
  messages: {},
  permissions: {},
  diff: null,
  todos: [],
  sessionError: null,
}

// ---------- ordering ----------

/** opencode ids are time-ordered strings, so plain string order is creation order. */
function compareIds(a: string, b: string) {
  return a < b ? -1 : a > b ? 1 : 0
}

export function sortedMessages(map: MessageMap): MessageWithParts[] {
  return Object.entries(map)
    .filter((entry): entry is [string, MessageEntry & { info: MessageInfo }] => Boolean(entry[1].info))
    .sort(([ida, a], [idb, b]) => {
      const ta = a.info.time?.created ?? 0
      const tb = b.info.time?.created ?? 0
      return ta !== tb ? ta - tb : compareIds(ida, idb)
    })
    .map(([, entry]) => ({ info: entry.info, parts: sortedParts(entry) }))
}

export function sortedParts(entry: MessageEntry): Part[] {
  return Object.values(entry.parts).sort((a, b) => compareIds(a.id, b.id))
}

export function sortedSessions(sessions: Record<string, Session>): Session[] {
  return Object.values(sessions)
    .filter((s) => !s.parentID)
    .sort((a, b) => (b.time?.updated ?? 0) - (a.time?.updated ?? 0))
}

// ---------- message map updates ----------

export function upsertInfo(map: MessageMap, info: MessageInfo): MessageMap {
  const existing = map[info.id]
  return { ...map, [info.id]: { info, parts: existing?.parts ?? {} } }
}

export function upsertPart(map: MessageMap, part: Part): MessageMap {
  const existing = map[part.messageID] ?? { parts: {} }
  return { ...map, [part.messageID]: { ...existing, parts: { ...existing.parts, [part.id]: part } } }
}

/** message.part.delta: append streamed text to one field of a part. */
export function applyDelta(
  map: MessageMap,
  d: { messageID: string; partID: string; field: string; delta: string },
): MessageMap {
  const entry = map[d.messageID] ?? { parts: {} }
  const part: Part = entry.parts[d.partID] ?? { id: d.partID, messageID: d.messageID, type: "pending" }
  const current = typeof part[d.field] === "string" ? (part[d.field] as string) : ""
  return upsertPart(map, { ...part, [d.field]: current + d.delta })
}

export function removeMessage(map: MessageMap, messageID: string): MessageMap {
  if (!map[messageID]) return map
  const next = { ...map }
  delete next[messageID]
  return next
}

export function removePart(map: MessageMap, messageID: string, partID: string): MessageMap {
  const entry = map[messageID]
  if (!entry?.parts[partID]) return map
  const parts = { ...entry.parts }
  delete parts[partID]
  return { ...map, [messageID]: { ...entry, parts } }
}

const TOOL_RANK: Record<string, number> = { pending: 0, running: 1, completed: 2, error: 2 }

/** Of two copies of the same part, keep the one that is further along. Live wins ties. */
function pickPart(live: Part, snapshot: Part): Part {
  if (live.type === "pending") return snapshot
  if (live.type === "tool" && snapshot.type === "tool") {
    const l = TOOL_RANK[live.state?.status ?? "pending"] ?? 0
    const s = TOOL_RANK[snapshot.state?.status ?? "pending"] ?? 0
    return s > l ? snapshot : live
  }
  if (typeof live.text === "string" && typeof snapshot.text === "string") {
    return snapshot.text.length > live.text.length ? snapshot : live
  }
  return live
}

/**
 * Merge a freshly fetched history snapshot into live state.
 *
 * Events keep streaming while the history request is in flight. Replacing
 * state with the snapshot would drop parts that arrived in between (the first
 * tool call would vanish), so live data wins and the snapshot only fills gaps.
 */
export function mergeSnapshot(live: MessageMap, snapshot: { info: MessageInfo; parts: Part[] }[]): MessageMap {
  const merged: MessageMap = { ...live }
  for (const { info, parts } of snapshot) {
    const current = live[info.id]
    const mergedParts: Record<string, Part> = {}
    for (const part of parts) mergedParts[part.id] = part
    for (const [partID, livePart] of Object.entries(current?.parts ?? {})) {
      const snapPart = mergedParts[partID]
      mergedParts[partID] = snapPart ? pickPart(livePart, snapPart) : livePart
    }
    merged[info.id] = { info: current?.info ?? info, parts: mergedParts }
  }
  return merged
}

/** The session's file changes, rebuilt from the per-turn diffs on user messages. */
export function diffFromMessages(map: MessageMap): FileDiff[] {
  return aggregateTurnDiffs(Object.values(map).flatMap((entry) => (entry.info ? [entry.info] : [])))
}

// ---------- permissions ----------

export function normalizePermission(raw: RawPermission): Permission {
  const kind = raw.permission ?? raw.type ?? raw.action ?? "action"
  const patterns = raw.patterns ?? raw.resources ?? (raw.pattern ? ([] as string[]).concat(raw.pattern) : [])
  const meta = raw.metadata ?? {}
  const details: string[] = []
  for (const key of ["command", "filePath", "path", "url"]) {
    const value = meta[key]
    if (typeof value === "string" && value) details.push(value)
  }
  for (const p of patterns) if (p && p !== "*" && !details.includes(p)) details.push(p)
  const title = raw.title ?? describePermission(kind)
  return { id: raw.id, sessionID: raw.sessionID, title, details, kind, ...(raw.tool?.callID ? { callID: raw.tool.callID } : {}) }
}

function describePermission(kind: string): string {
  switch (kind) {
    case "bash":
      return "Run a shell command"
    case "edit":
    case "write":
    case "patch":
      return "Edit files"
    case "webfetch":
      return "Fetch a web page"
    case "external_directory":
      return "Use a folder outside the project"
    default:
      return describeAppTool(kind) ?? `Allow "${kind}"`
  }
}

// ---------- events ----------

/** Events may arrive bare or wrapped as { payload } (global stream). */
export function unwrapEvent(data: unknown): OcEvent | null {
  if (!data || typeof data !== "object") return null
  const d = data as { type?: unknown; properties?: unknown; payload?: unknown }
  if (d.payload && typeof d.payload === "object") return unwrapEvent(d.payload)
  if (typeof d.type !== "string") return null
  return { type: d.type, properties: (d.properties ?? {}) as Record<string, unknown> }
}

export type EventEffects = {
  /** The active session went idle; refresh its diff. */
  refreshDiff?: boolean
}

function errorMessage(error: unknown): string {
  if (!error || typeof error !== "object") return "The coding agent hit an error."
  const e = error as { name?: string; data?: { message?: string } }
  if (e.name === "MessageAbortedError") return "Stopped."
  return e.data?.message || e.name || "The coding agent hit an error."
}

/** Apply one opencode event. Returns the next state plus side effects to run. */
export function reduceEvent(
  state: AgentState,
  event: OcEvent,
  activeSessionId: string | null,
): { state: AgentState; effects: EventEffects } {
  const p = event.properties
  const sessionID = (p.sessionID as string | undefined) ?? (p.info as { sessionID?: string } | undefined)?.sessionID
  const isActive = Boolean(activeSessionId) && sessionID === activeSessionId
  const none = { state, effects: {} }

  switch (event.type) {
    case "session.created":
    case "session.updated": {
      const info = p.info as Session | undefined
      if (!info?.id) return none
      return { state: { ...state, sessions: { ...state.sessions, [info.id]: info } }, effects: {} }
    }
    case "session.deleted": {
      const id = (p.info as Session | undefined)?.id ?? (p.sessionID as string | undefined)
      if (!id || !state.sessions[id]) return none
      const sessions = { ...state.sessions }
      delete sessions[id]
      return { state: { ...state, sessions }, effects: {} }
    }
    case "session.status": {
      const status = (p.status as SessionStatus | undefined)?.type
      if (!sessionID || !status) return none
      const wasBusy = state.status[sessionID] === "busy"
      const next = { ...state, status: { ...state.status, [sessionID]: status } }
      return { state: next, effects: { refreshDiff: isActive && wasBusy && status === "idle" } }
    }
    case "session.idle": {
      if (!sessionID) return none
      return {
        state: { ...state, status: { ...state.status, [sessionID]: "idle" } },
        effects: { refreshDiff: isActive },
      }
    }
    case "session.error": {
      if (sessionID && !isActive) return none
      return { state: { ...state, sessionError: errorMessage(p.error) }, effects: {} }
    }
    case "session.diff": {
      // opencode 1.18 sends an empty session.diff at the start of every turn
      // summary; real diffs arrive on user messages (see message.updated).
      if (!isActive || !Array.isArray(p.diff) || (p.diff.length === 0 && state.diff !== null)) return none
      return { state: { ...state, diff: p.diff as FileDiff[] }, effects: {} }
    }
    case "todo.updated": {
      if (!isActive || !Array.isArray(p.todos)) return none
      return { state: { ...state, todos: p.todos as Todo[] }, effects: {} }
    }
    case "message.updated": {
      const info = p.info as MessageInfo | undefined
      if (!info || !isActive) return none
      const messages = upsertInfo(state.messages, info)
      if (info.role === "user" && info.summary?.diffs?.length) {
        const diff = diffFromMessages(messages)
        return { state: { ...state, messages, diff }, effects: {} }
      }
      return { state: { ...state, messages }, effects: {} }
    }
    case "message.removed": {
      if (!isActive) return none
      return { state: { ...state, messages: removeMessage(state.messages, p.messageID as string) }, effects: {} }
    }
    case "message.part.updated": {
      const part = p.part as Part | undefined
      if (!part || (part.sessionID ?? sessionID) !== activeSessionId) return none
      return { state: { ...state, messages: upsertPart(state.messages, part) }, effects: {} }
    }
    case "message.part.delta": {
      if (!isActive || typeof p.delta !== "string") return none
      const d = p as { messageID: string; partID: string; field: string; delta: string }
      return { state: { ...state, messages: applyDelta(state.messages, d) }, effects: {} }
    }
    case "message.part.removed": {
      if (!isActive) return none
      return {
        state: { ...state, messages: removePart(state.messages, p.messageID as string, p.partID as string) },
        effects: {},
      }
    }
    case "permission.updated":
    case "permission.asked":
    case "permission.v2.asked": {
      const raw = p as unknown as RawPermission
      if (!raw.id || !raw.sessionID) return none
      return {
        state: { ...state, permissions: { ...state.permissions, [raw.id]: normalizePermission(raw) } },
        effects: {},
      }
    }
    case "permission.replied":
    case "permission.v2.replied": {
      const id = (p.requestID ?? p.permissionID ?? p.id) as string | undefined
      if (!id || !state.permissions[id]) return none
      const permissions = { ...state.permissions }
      delete permissions[id]
      return { state: { ...state, permissions }, effects: {} }
    }
    default:
      return none
  }
}
