import { requireUser } from "@/lib/auth/session"
import { OcError, ocJson, withOc } from "@/lib/opencode/server"
import { assertCanAct } from "@/lib/symphony/jobs"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string; permissionID: string }> }

const RESPONSES = ["once", "always", "reject"] as const

export const POST = withOc(async (req, { params }: Ctx) => {
  const { id, permissionID } = await params
  // Members answer only for jobs they started; the owner answers for anything.
  await assertCanAct(id, await requireUser(req))
  const body = (await req.json()) as { response?: unknown }
  const response = body.response
  if (typeof response !== "string" || !(RESPONSES as readonly string[]).includes(response)) {
    throw new OcError('response must be "once", "always" or "reject"', 400)
  }
  const ok = await ocJson<boolean>(
    `/session/${encodeURIComponent(id)}/permissions/${encodeURIComponent(permissionID)}`,
    { method: "POST", body: JSON.stringify({ response }) },
  )
  return Response.json({ ok: ok !== false })
})
