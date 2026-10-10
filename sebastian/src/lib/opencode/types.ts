// The slice of opencode's data model the Coding agent tab uses. Shapes were
// checked against opencode 1.18.35's OpenAPI spec (GET /doc). Fields are kept
// loose where versions differ.

export type Session = {
  id: string
  title?: string
  parentID?: string
  directory?: string
  time: { created: number; updated: number }
  summary?: { additions: number; deletions: number; files: number }
}

export type SessionStatus = { type: "busy" | "idle" | "retry"; attempt?: number; message?: string }

export type MessageInfo = {
  id: string
  sessionID: string
  role: "user" | "assistant"
  time: { created: number; completed?: number }
  error?: { name?: string; data?: { message?: string } }
  /** On user messages: the files that turn changed (opencode 1.18+). */
  summary?: { diffs?: FileDiff[] }
  modelID?: string
  providerID?: string
  model?: { providerID: string; modelID: string }
}

export type ToolState = {
  status: "pending" | "running" | "completed" | "error"
  input?: Record<string, unknown>
  output?: string
  error?: string
  title?: string
  raw?: string
}

export type Part = {
  id: string
  sessionID?: string
  messageID: string
  type: string
  text?: string
  synthetic?: boolean
  ignored?: boolean
  tool?: string
  callID?: string
  state?: ToolState
  files?: string[]
  filename?: string
  mime?: string
  url?: string
  [key: string]: unknown
}

export type MessageEntry = { info?: MessageInfo; parts: Record<string, Part> }
export type MessageMap = Record<string, MessageEntry>
export type MessageWithParts = { info: MessageInfo; parts: Part[] }

export type FileDiff = {
  file?: string
  patch?: string
  before?: string
  after?: string
  additions: number
  deletions: number
  status?: "added" | "deleted" | "modified"
}

export type Todo = { id?: string; content: string; status: string; priority?: string }

/** Permission request as opencode sends it; field names vary by version. */
export type RawPermission = {
  id: string
  sessionID: string
  permission?: string
  type?: string
  action?: string
  title?: string
  patterns?: string[]
  pattern?: string | string[]
  resources?: string[]
  metadata?: Record<string, unknown>
}

export type Permission = { id: string; sessionID: string; title: string; details: string[] }

export type ProviderSummary = { id: string; name: string; models: Record<string, { id: string; name: string }> }
export type ProvidersResponse = { providers: ProviderSummary[]; default: Record<string, string> }

export type OcEvent = { type: string; properties: Record<string, unknown> }
