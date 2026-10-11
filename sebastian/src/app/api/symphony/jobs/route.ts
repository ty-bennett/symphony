import { requireUser } from "@/lib/auth/session"
import { withOc } from "@/lib/opencode/server"
import { createJob, jobsWithSavings, spending, type NewJob } from "@/lib/symphony/jobs"
import { readSettings } from "@/lib/symphony/settings-store"
import type { Place } from "@/lib/symphony/types"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = withOc(async () => {
  const settings = await readSettings()
  const { jobs, savings } = await jobsWithSavings(settings)
  return Response.json({ jobs, spending: spending(jobs, settings), savings })
})

// Body: { text, checkFirst?, place? } -> 202 { jobId, place, reason }
export const POST = withOc(async (req) => {
  const body = (await req.json()) as Record<string, unknown>
  const input: NewJob = {
    text: typeof body.text === "string" ? body.text : "",
    checkFirst: typeof body.checkFirst === "boolean" ? body.checkFirst : undefined,
    place: typeof body.place === "string" ? (body.place as Place) : undefined,
  }
  const created = await createJob(input, await readSettings(), await requireUser(req))
  return Response.json(created, { status: 202 })
})
