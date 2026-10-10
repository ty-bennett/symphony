import { withOc } from "@/lib/opencode/server"
import { redoJob } from "@/lib/symphony/jobs"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const POST = withOc(async (_req, { params }: Ctx) => {
  const { id } = await params
  await redoJob(id)
  return Response.json({ undone: false })
})
