import { oc, unwrap, withOc } from "@/lib/opencode/server"

import { appPermissionRules } from "@/lib/symphony/apps"
import { appContext } from "@/lib/symphony/jobs"
import { readSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = withOc(async () => {
  const sessions = unwrap(await oc().session.list()) ?? []
  sessions.sort((a, b) => (b.time?.updated ?? 0) - (a.time?.updated ?? 0))
  return Response.json(sessions)
})

export const POST = withOc(async (req) => {
  const body = (await req.json().catch(() => ({}))) as { title?: unknown }
  const title = typeof body.title === "string" && body.title.trim() ? body.title.trim().slice(0, 120) : undefined
  // The coding agent keeps opencode's own file/shell rules; n8n's tools get Symphony's guard.
  const settings = await readSettings()
  const permission = appPermissionRules({ enabled: (await appContext(settings)).ready, ask: true })
  const session = unwrap(await oc().session.create({ ...(title ? { title } : {}), permission }))
  return Response.json(session, { status: 201 })
})
