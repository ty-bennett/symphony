import { requireUser } from "@/lib/auth/session"
import { removeUser } from "@/lib/auth/store"
import { ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

// Removes a member and signs them out everywhere. The owner can't be removed.
export const DELETE = ownerOnly(async (req, { params }: Ctx) => {
  await removeUser((await params).id, await requireUser(req))
  return Response.json({ ok: true })
})
