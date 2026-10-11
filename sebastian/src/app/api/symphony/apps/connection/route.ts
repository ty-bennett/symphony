import { OcError, withOc } from "@/lib/opencode/server"
import { appsOverview } from "@/lib/symphony/apps-server"
import {
  cleanUrl,
  clearN8nConfig,
  ensureRegistered,
  forgetWorkflows,
  listWorkflows,
  readN8nConfig,
  saveN8nConfig,
  unregister,
} from "@/lib/symphony/n8n"
import { readSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// Body: { url, token }. Tests the connection before saving it.
export const PUT = withOc(async (req) => {
  const body = (await req.json()) as { url?: unknown; token?: unknown }
  const url = typeof body.url === "string" ? cleanUrl(body.url) : ""
  const token = typeof body.token === "string" ? body.token.trim() : ""
  if (!/^https?:\/\/[^\s]+$/.test(url)) throw new OcError("Enter n8n's address, like http://localhost:5678.", 400)
  if (!token) throw new OcError("Paste the access token from n8n.", 400)
  const current = await readN8nConfig()
  if (current?.source === "env") throw new OcError("n8n is set with N8N_URL and N8N_MCP_TOKEN. Change it there.", 409)
  const config = { url, token, source: "saved" as const }
  await listWorkflows(config) // throws a readable error if it doesn't work
  await saveN8nConfig(url, token)
  forgetWorkflows()
  // If opencode is down, the connection is still saved; the page shows the agent's status.
  await ensureRegistered(config).catch(() => undefined)
  return Response.json(await appsOverview(await readSettings(), true))
})

export const DELETE = withOc(async () => {
  const current = await readN8nConfig()
  if (current?.source === "env") throw new OcError("n8n is set with N8N_URL and N8N_MCP_TOKEN. Remove those to disconnect.", 409)
  await clearN8nConfig()
  forgetWorkflows()
  await unregister()
  return Response.json(await appsOverview(await readSettings(), true))
})
