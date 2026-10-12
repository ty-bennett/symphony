import { oc, unwrap, ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = ownerOnly(async () => {
  const sessions = unwrap(await oc().session.list()) ?? []
  sessions.sort((a, b) => (b.time?.updated ?? 0) - (a.time?.updated ?? 0))
  return Response.json(sessions)
})

export const POST = ownerOnly(async (req) => {
  const body = (await req.json().catch(() => ({}))) as { title?: unknown }
  const title = typeof body.title === "string" && body.title.trim() ? body.title.trim().slice(0, 120) : undefined
  const session = unwrap(await oc().session.create(title ? { title } : {}))
  return Response.json(session, { status: 201 })
})
