import { oc, unwrap, withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const DELETE = withOc(async (_req, { params }: Ctx) => {
  const { id } = await params
  unwrap(await oc().session.delete({ sessionID: id }))
  return new Response(null, { status: 204 })
})
