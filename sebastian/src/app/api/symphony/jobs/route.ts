import { withOc } from "@/lib/opencode/server"
import { ConsentRequired, createJob, listJobs, spending, type NewJob } from "@/lib/symphony/jobs"
import { readSettings } from "@/lib/symphony/settings-store"
import type { Place } from "@/lib/symphony/types"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = withOc(async () => {
  const settings = await readSettings()
  const jobs = await listJobs(settings)
  return Response.json({ jobs, spending: spending(jobs, settings) })
})

// Body: { text, checkFirst?, place?, consent?, redact? } -> 202 { jobId, place, reason }
// 409 { needsConsent, decision } when private info would go to the open cloud.
export const POST = withOc(async (req) => {
  const body = (await req.json()) as Record<string, unknown>
  const input: NewJob = {
    text: typeof body.text === "string" ? body.text : "",
    checkFirst: typeof body.checkFirst === "boolean" ? body.checkFirst : undefined,
    place: typeof body.place === "string" ? (body.place as Place) : undefined,
    consent: body.consent === true,
    redact: body.redact === true,
  }
  try {
    const created = await createJob(input, await readSettings())
    return Response.json(created, { status: 202 })
  } catch (error) {
    if (error instanceof ConsentRequired) {
      return Response.json({ error: error.message, needsConsent: true, decision: error.decision }, { status: 409 })
    }
    throw error
  }
})
