import { oc, parseModel, readPromptInput, sendPrompt, unwrap, ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

/**
 * Create a session and send its first prompt in one call. This is the entry
 * point for Symphony's orchestration engine.
 *
 * Body: { text, title?, model?: "provider/model", agent? } -> 202 { sessionId }
 */
export const POST = ownerOnly(async (req) => {
  const body = (await req.json()) as Record<string, unknown>
  const input = readPromptInput(body)
  parseModel(input.model) // validate before creating anything
  const title =
    typeof body.title === "string" && body.title.trim()
      ? body.title.trim().slice(0, 120)
      : input.text.trim().split("\n")[0].slice(0, 80)
  const session = unwrap(await oc().session.create({ title }))
  await sendPrompt(session.id, input)
  return Response.json({ sessionId: session.id }, { status: 202 })
})
