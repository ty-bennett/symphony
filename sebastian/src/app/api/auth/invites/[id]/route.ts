import { revokeInvite } from "@/lib/auth/store"
import { ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const DELETE = ownerOnly(async (_req, { params }: Ctx) => {
  await revokeInvite((await params).id)
  return Response.json({ ok: true })
})
