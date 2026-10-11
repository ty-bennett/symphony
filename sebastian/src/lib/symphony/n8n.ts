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

async function rpc(
  config: N8nConfig,
  body: Record<string, unknown>,
  session?: string,
): Promise<{ message?: RpcResponse; session?: string }> {
  let response: Response
  try {
    response = await fetch(mcpUrl(config), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        authorization: `Bearer ${config.token}`,
        "mcp-protocol-version": "2025-06-18",
        ...(session ? { "mcp-session-id": session } : {}),
      },
      body: JSON.stringify({ jsonrpc: "2.0", ...body }),
      signal: AbortSignal.timeout(15_000),
    })
  } catch {
    throw new OcError(`Can't reach n8n at ${config.url}. Is it running?`, 502)
  }
  if (response.status === 401 || response.status === 403) {
    throw new OcError("n8n didn't accept the access token. Generate a new one in n8n under Settings > Instance-level MCP.", 401)
  }
  if (response.status === 404) {
    throw new OcError("n8n's MCP server isn't turned on. In n8n, open Settings > Instance-level MCP and enable it.", 404)
  }
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

async function callTool(config: N8nConfig, name: string, args: Record<string, unknown>): Promise<unknown> {
  const init = await rpc(config, {
    id: 1,
    method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "symphony", version: "0.1.0" } },
  })
  await rpc(config, { method: "notifications/initialized" }, init.session)
  const { message } = await rpc(config, { id: 2, method: "tools/call", params: { name, arguments: args } }, init.session)
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

/** Workflows the agent can see: active and "Available in MCP". */
export async function listWorkflows(config: N8nConfig): Promise<AppWorkflow[]> {
  const out = (await callTool(config, "search_workflows", { limit: 100 })) as {
    data?: { id: string; name?: string | null; description?: string | null; active?: boolean | null; availableInMCP?: boolean; tags?: unknown[] }[]
    error?: string
  }
  if (out?.error) throw new OcError(`n8n: ${out.error}`, 502)
  return (out?.data ?? []).map((w) => ({
    id: String(w.id),
    name: w.name || "Untitled workflow",
    description: w.description ?? null,
    active: Boolean(w.active),
    availableInMCP: Boolean(w.availableInMCP),
    tags: (w.tags ?? [])
      .map((t) => (typeof t === "string" ? t : (t as { name?: string })?.name))
      .filter((t): t is string => typeof t === "string"),
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
