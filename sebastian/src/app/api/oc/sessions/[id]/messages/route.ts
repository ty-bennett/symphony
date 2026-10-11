import { oc, unwrap, withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

// Returns [{ info, parts }] in opencode's order.
export const GET = withOc(async (_req, { params }: Ctx) => {
  const { id } = await params
  const messages = unwrap(await oc().session.messages({ sessionID: id })) ?? []
  return Response.json(messages)
})
