import { withOc } from "@/lib/opencode/server"
import { connectApp, disconnectApp } from "@/lib/symphony/app-servers"
import { appsOverview } from "@/lib/symphony/apps-server"
import { readSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

// Body: { url, token? } — the Production URL of the app's MCP Server Trigger
// workflow in n8n, and its Bearer token. Tested (tools are listed) before saving.
export const PUT = withOc(async (req, { params }: Ctx) => {
  const { id } = await params
  const body = (await req.json()) as { url?: unknown; token?: unknown }
  await connectApp(id, typeof body.url === "string" ? body.url : "", typeof body.token === "string" ? body.token : undefined)
  return Response.json(await appsOverview(await readSettings(), true))
})

export const DELETE = withOc(async (_req, { params }: Ctx) => {
  const { id } = await params
  await disconnectApp(id)
  return Response.json(await appsOverview(await readSettings(), true))
})
