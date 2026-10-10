import { withOc } from "@/lib/opencode/server"
import { getJob } from "@/lib/symphony/jobs"
import { readSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const GET = withOc(async (_req, { params }: Ctx) => {
  const { id } = await params
  const { job, meta } = await getJob(id, await readSettings())
  return Response.json({ job, redactions: meta?.redactions ?? {} })
})
