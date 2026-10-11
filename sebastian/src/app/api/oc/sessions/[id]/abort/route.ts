import { oc, unwrap, ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const POST = ownerOnly(async (_req, { params }: Ctx) => {
  const { id } = await params
  const aborted = unwrap(await oc().session.abort({ sessionID: id }))
  return Response.json({ aborted: Boolean(aborted) })
})
