import { requireUser } from "@/lib/auth/session"
import { withOc } from "@/lib/opencode/server"
import { assertCanAct, undoJob } from "@/lib/symphony/jobs"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

// Restores every file the job changed and hides its messages until redone.
export const POST = withOc(async (req, { params }: Ctx) => {
  const { id } = await params
  await assertCanAct(id, await requireUser(req))
  await undoJob(id)
  return Response.json({ undone: true })
})
