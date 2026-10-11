// Server-only: the connection to n8n, which powers Connected apps.
//
// Symphony talks to n8n's built-in (instance-level) MCP server, at
// <n8n address>/mcp-server/http with a bearer token. It uses it for two things:
//   1. listing the workflows marked "Available in MCP", to show what the agent
//      can do in each app, and
//   2. registering the same server with opencode, so jobs can run them.
// The token never leaves the server.
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises"
import path from "node:path"
import { OcError, ocJson } from "@/lib/opencode/server"
import { N8N_SERVER, type AppWorkflow } from "./apps"
import { dataDir } from "./settings-store"

export type N8nConfig = { url: string; token: string; source: "env" | "saved" }

const file = () => path.join(dataDir(), "n8n.json")

/** N8N_URL / N8N_MCP_TOKEN in the environment win over what was saved from the app. */
export async function readN8nConfig(): Promise<N8nConfig | null> {
  const envUrl = process.env.N8N_URL?.trim()
  const envToken = process.env.N8N_MCP_TOKEN?.trim()
  if (envUrl && envToken) return { url: cleanUrl(envUrl), token: envToken, source: "env" }
  try {
    const saved = JSON.parse(await readFile(file(), "utf8")) as { url?: string; token?: string }
    if (saved.url && saved.token) return { url: cleanUrl(saved.url), token: saved.token, source: "saved" }
  } catch {
    // Not set up yet.
  }
  return null
}

export async function saveN8nConfig(url: string, token: string) {
  await mkdir(dataDir(), { recursive: true })
  const tmp = `${file()}.${process.pid}.tmp`
  await writeFile(tmp, JSON.stringify({ url: cleanUrl(url), token }, null, 2), { mode: 0o600 })
  await rename(tmp, file())
}

export async function clearN8nConfig() {
  await rm(file(), { force: true })
}

/** Accepts "http://box.local:5678", ".../", or the full ".../mcp-server/http". */
export function cleanUrl(input: string): string {
  return input.trim().replace(/\/+$/, "").replace(/\/mcp-server\/http$/, "")
}

export const mcpUrl = (config: Pick<N8nConfig, "url">) => `${config.url}/mcp-server/http`

// ---------- a small MCP client (streamable HTTP, JSON-RPC) ----------

type RpcResponse = { id?: number | string; result?: unknown; error?: { code: number; message: string } }

/** Where to send MCP requests, and what to tell the owner when it fails. */
export type McpTarget = { endpoint: string; token?: string; name: string; authHint: string; missingHint: string }

const instanceTarget = (config: N8nConfig): McpTarget => ({
  endpoint: mcpUrl(config),
  token: config.token,
  name: `n8n at ${config.url}`,
  authHint: "n8n didn't accept the access token. Generate a new one in n8n under Settings > Instance-level MCP.",
  missingHint: "n8n's MCP server isn't turned on. In n8n, open Settings > Instance-level MCP and enable it.",
})

async function rpc(target: McpTarget, body: Record<string, unknown>, session?: string): Promise<{ message?: RpcResponse; session?: string }> {
  let response: Response
  try {
    response = await fetch(target.endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        "mcp-protocol-version": "2025-06-18",
        ...(target.token ? { authorization: `Bearer ${target.token}` } : {}),
        ...(session ? { "mcp-session-id": session } : {}),
      },
      body: JSON.stringify({ jsonrpc: "2.0", ...body }),
      signal: AbortSignal.timeout(15_000),
    })
  } catch {
    throw new OcError(`Can't reach ${target.name}. Is n8n running?`, 502)
  }
  if (response.status === 401 || response.status === 403) throw new OcError(target.authHint, 401)
  if (response.status === 404) throw new OcError(target.missingHint, 404)
  const nextSession = response.headers.get("mcp-session-id") ?? session
  if (response.status === 202 || !("id" in body)) return { session: nextSession }
  if (!response.ok) throw new OcError(`n8n returned ${response.status}`, 502)
  const text = await response.text()
  const type = response.headers.get("content-type") ?? ""
  let message: RpcResponse | undefined
  if (type.includes("text/event-stream")) {
    // One or more SSE events; take the JSON-RPC response with our id.
    for (const line of text.split(/\r?\n/)) {
      if (!line.startsWith("data:")) continue
      try {
        const data = JSON.parse(line.slice(5).trim()) as RpcResponse
        if (data.id === body.id) message = data
      } catch {
        // Not JSON; skip.
      }
    }
  } else if (text) {
    message = JSON.parse(text) as RpcResponse
  }
  if (message?.error) throw new OcError(`n8n: ${message.error.message}`, 502)
  return { message, session: nextSession }
}

/** Open a session, run one request, close the session. */
async function withSession<T>(target: McpTarget, run: (session?: string) => Promise<T>): Promise<T> {
  const init = await rpc(target, {
    id: 1,
    method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "symphony", version: "0.1.0" } },
  })
  await rpc(target, { method: "notifications/initialized" }, init.session)
  try {
    return await run(init.session)
  } finally {
    if (init.session) {
      fetch(target.endpoint, {
        method: "DELETE",
        headers: { "mcp-session-id": init.session, ...(target.token ? { authorization: `Bearer ${target.token}` } : {}) },
        signal: AbortSignal.timeout(5_000),
      }).catch(() => undefined)
    }
  }
}

async function callTool(config: N8nConfig, name: string, args: Record<string, unknown>): Promise<unknown> {
  const { message } = await withSession(instanceTarget(config), (session) =>
    rpc(instanceTarget(config), { id: 2, method: "tools/call", params: { name, arguments: args } }, session),
  )
  const result = message?.result as
    | { structuredContent?: unknown; content?: { type: string; text?: string }[]; isError?: boolean }
    | undefined
  if (!result) throw new OcError("n8n sent an empty reply.", 502)
  const text = result.content?.find((c) => c.type === "text")?.text
  if (result.isError) throw new OcError(`n8n: ${text ?? "the request failed"}`, 502)
  if (result.structuredContent !== undefined) return result.structuredContent
  try {
    return text ? JSON.parse(text) : undefined
  } catch {
    return text
  }
}

export type AppTool = { name: string; description: string }

/** The tools an app's MCP Server Trigger workflow offers. */
export async function listTools(target: McpTarget): Promise<AppTool[]> {
  const { message } = await withSession(target, (session) => rpc(target, { id: 2, method: "tools/list", params: {} }, session))
  const tools = (message?.result as { tools?: { name?: string; description?: string }[] } | undefined)?.tools ?? []
  return tools.filter((t) => typeof t.name === "string").map((t) => ({ name: t.name!, description: t.description ?? "" }))
}

/** Workflows the agent can see: active and "Available in MCP". */
export async function listWorkflows(config: N8nConfig): Promise<AppWorkflow[]> {
  const out = (await callTool(config, "search_workflows", { limit: 100 })) as {
    data?: {
      id: string
      name?: string | null
      description?: string | null
      active?: boolean | null
      availableInMCP?: boolean
      tags?: unknown[]
      nodes?: { type?: string }[]
    }[]
    error?: string
  }
  if (out?.error) throw new OcError(`n8n: ${out.error}`, 502)
  return (out?.data ?? []).map((w) => ({
    id: String(w.id),
    name: w.name || "Untitled workflow",
    description: w.description ?? null,
    active: Boolean(w.active),
    // n8n 2.8 lists only workflows already enabled for MCP and has no such field.
    availableInMCP: w.availableInMCP ?? true,
    tags: (w.tags ?? [])
      .map((t) => (typeof t === "string" ? t : (t as { name?: string })?.name))
      .filter((t): t is string => typeof t === "string"),
    nodes: (w.nodes ?? []).map((n) => n.type).filter((t): t is string => typeof t === "string"),
  }))
}

// A short cache so starting a job doesn't wait on n8n every time.
const cache = globalThis as unknown as { __symphonyN8n?: { at: number; key: string; workflows: AppWorkflow[] } }

export async function cachedWorkflows(config: N8nConfig, maxAge = 30_000): Promise<AppWorkflow[]> {
  const key = `${config.url}|${config.token}`
  const hit = cache.__symphonyN8n
  if (hit && hit.key === key && Date.now() - hit.at < maxAge) return hit.workflows
  const workflows = await listWorkflows(config)
  cache.__symphonyN8n = { at: Date.now(), key, workflows }
  return workflows
}

export function forgetWorkflows() {
  cache.__symphonyN8n = undefined
}

// ---------- opencode registration ----------

type McpStatus = { status: string; error?: string }

const registered = globalThis as unknown as { __symphonyN8nRegistered?: string }

/** Make sure opencode has n8n's MCP server, with the current address and token. */
export async function ensureRegistered(config: N8nConfig): Promise<McpStatus> {
  const key = `${config.url}|${config.token}`
  const statuses = await ocJson<Record<string, McpStatus>>("/mcp").catch(() => ({}) as Record<string, McpStatus>)
  const current = statuses?.[N8N_SERVER]
  if (current?.status === "connected" && registered.__symphonyN8nRegistered === key) return current
  const result = await ocJson<Record<string, McpStatus>>("/mcp", {
    method: "POST",
    body: JSON.stringify({
      name: N8N_SERVER,
      config: {
        type: "remote",
        url: mcpUrl(config),
        headers: { Authorization: `Bearer ${config.token}` },
        oauth: false,
        enabled: true,
      },
    }),
  })
  registered.__symphonyN8nRegistered = key
  return result?.[N8N_SERVER] ?? { status: "unknown" }
}

export async function unregister() {
  registered.__symphonyN8nRegistered = undefined
  await ocJson(`/mcp/${N8N_SERVER}/disconnect`, { method: "POST" }).catch(() => undefined)
}

export async function opencodeStatus(): Promise<McpStatus | null> {
  const statuses = await ocJson<Record<string, McpStatus>>("/mcp").catch(() => null)
  return statuses?.[N8N_SERVER] ?? null
}
