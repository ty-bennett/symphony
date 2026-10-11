import { readPromptInput, sendPrompt, ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

// Body: { text, model?: "provider/model", agent? }. Replies 202 once opencode
// has accepted the prompt; the answer streams in over /api/oc/events.
export const POST = ownerOnly(async (req, { params }: Ctx) => {
  const { id } = await params
  const input = readPromptInput(await req.json())
  await sendPrompt(id, input)
  return Response.json({ accepted: true }, { status: 202 })
})
