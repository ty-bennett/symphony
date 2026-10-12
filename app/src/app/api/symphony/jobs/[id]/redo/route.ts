import { requireUser } from "@/lib/auth/session"
import { withOc } from "@/lib/opencode/server"
import { assertCanAct, redoJob } from "@/lib/symphony/jobs"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const POST = withOc(async (req, { params }: Ctx) => {
  const { id } = await params
  await assertCanAct(id, await requireUser(req))
  await redoJob(id)
  return Response.json({ undone: false })
})
