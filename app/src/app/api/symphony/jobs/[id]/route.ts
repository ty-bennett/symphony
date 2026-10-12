import { withOc } from "@/lib/opencode/server"
import { compareRates, getJob } from "@/lib/symphony/jobs"
import { withSavings } from "@/lib/symphony/savings"
import { readSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const GET = withOc(async (_req, { params }: Ctx) => {
  const { id } = await params
  const settings = await readSettings()
  const [{ job }, rates] = await Promise.all([getJob(id, settings), compareRates(settings)])
  return Response.json({ job: withSavings(job, rates), rates })
})
