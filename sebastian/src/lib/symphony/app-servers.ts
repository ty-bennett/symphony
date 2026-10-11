// Server-only: per-app connections. Each connected app is an n8n workflow
// whose MCP Server Trigger has that app's tool nodes attached (Gmail: search,
// read, send, reply, draft...). Symphony registers each one with opencode
// under the app's id, so the agent gets tools like `gmail_send_email`.
import { mkdir, readFile, rename, writeFile } from "node:fs/promises"
import path from "node:path"
import { OcError, ocJson } from "@/lib/opencode/server"
import { APPS } from "./apps"
import { listTools, type AppTool, type McpTarget } from "./n8n"
import { dataDir } from "./settings-store"

export type AppServer = { url: string; token?: string }

const file = () => path.join(dataDir(), "app-servers.json")

export async function readAppServers(): Promise<Record<string, AppServer>> {
  try {
    const raw = JSON.parse(await readFile(file(), "utf8")) as Record<string, AppServer>
    const known = new Set(APPS.map((a) => a.id))
    return Object.fromEntries(Object.entries(raw).filter(([id, s]) => known.has(id) && typeof s?.url === "string"))
  } catch {
    return {}
  }
}

async function writeAppServers(all: Record<string, AppServer>) {
  await mkdir(dataDir(), { recursive: true })
  const tmp = `${file()}.${process.pid}.tmp`
  await writeFile(tmp, JSON.stringify(all, null, 2), { mode: 0o600 })
  await rename(tmp, file())
}

export function appTarget(appId: string, server: AppServer): McpTarget {
  const name = APPS.find((a) => a.id === appId)?.name ?? appId
  return {
    endpoint: server.url,
    token: server.token,
    name: `the ${name} workflow in n8n`,
    authHint: `n8n didn't accept the token for ${name}. Use the same token as the Bearer Auth credential on the workflow's MCP Server Trigger.`,
    missingHint: `n8n doesn't know that address. Copy the Production URL from the ${name} workflow's MCP Server Trigger, and make sure the workflow is active.`,
  }
}

/** Accepts the MCP Server Trigger's Production URL, e.g. http://localhost:5678/mcp/symphony-gmail */
export function checkUrl(input: string): string {
  const url = input.trim().replace(/\/+$/, "")
  if (!/^https?:\/\/[^\s]+$/.test(url)) throw new OcError("Paste the Production URL from the workflow's MCP Server Trigger.", 400)
  if (/\/mcp-test\//.test(url)) {
    throw new OcError("That's the test URL, which only works while n8n's editor is listening. Use the Production URL instead.", 400)
  }
  return url
}

const cache = globalThis as unknown as { __symphonyAppTools?: Map<string, { at: number; key: string; tools: AppTool[] }> }

export async function cachedTools(appId: string, server: AppServer, maxAge = 60_000): Promise<AppTool[]> {
  cache.__symphonyAppTools ??= new Map()
  const key = `${server.url}|${server.token ?? ""}`
  const hit = cache.__symphonyAppTools.get(appId)
  if (hit && hit.key === key && Date.now() - hit.at < maxAge) return hit.tools
  const tools = await listTools(appTarget(appId, server))
  cache.__symphonyAppTools.set(appId, { at: Date.now(), key, tools })
  return tools
}

type McpStatus = { status: string; error?: string }
const registered = globalThis as unknown as { __symphonyAppRegistered?: Map<string, string> }

/** Make sure opencode has this app's MCP server, with the current address and token. */
export async function ensureAppRegistered(appId: string, server: AppServer): Promise<McpStatus> {
  registered.__symphonyAppRegistered ??= new Map()
  const key = `${server.url}|${server.token ?? ""}`
  const statuses = await ocJson<Record<string, McpStatus>>("/mcp").catch(() => ({}) as Record<string, McpStatus>)
  const current = statuses?.[appId]
  if (current?.status === "connected" && registered.__symphonyAppRegistered.get(appId) === key) return current
  const result = await ocJson<Record<string, McpStatus>>("/mcp", {
    method: "POST",
    body: JSON.stringify({
      name: appId,
      config: {
        type: "remote",
        url: server.url,
        ...(server.token ? { headers: { Authorization: `Bearer ${server.token}` } } : {}),
        oauth: false,
        enabled: true,
      },
    }),
  })
  registered.__symphonyAppRegistered.set(appId, key)
  return result?.[appId] ?? { status: "unknown" }
}

export async function connectApp(appId: string, url: string, token?: string): Promise<AppTool[]> {
  if (!APPS.some((a) => a.id === appId)) throw new OcError("Unknown app.", 404)
  const server: AppServer = { url: checkUrl(url), ...(token?.trim() ? { token: token.trim() } : {}) }
  const tools = await listTools(appTarget(appId, server)) // throws a readable error if it doesn't work
  if (!tools.length) {
    throw new OcError("Connected, but the workflow has no tools. Attach the app's tool nodes to its MCP Server Trigger.", 400)
  }
  const all = await readAppServers()
  all[appId] = server
  await writeAppServers(all)
  cache.__symphonyAppTools?.delete(appId)
  await ensureAppRegistered(appId, server).catch(() => undefined)
  return tools
}

export async function disconnectApp(appId: string) {
  const all = await readAppServers()
  delete all[appId]
  await writeAppServers(all)
  cache.__symphonyAppTools?.delete(appId)
  registered.__symphonyAppRegistered?.delete(appId)
  await ocJson(`/mcp/${encodeURIComponent(appId)}/disconnect`, { method: "POST" }).catch(() => undefined)
}
