import { aggregateTurnDiffs } from "@/lib/opencode/diff"
import { oc, ocJson, unwrap, withOc } from "@/lib/opencode/server"
import type { FileDiff, MessageInfo } from "@/lib/opencode/types"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

// Older servers return the whole session's diff here as
// { file, before, after, additions, deletions }. opencode 1.18 returns [] for
// the session and keeps a { file, patch, additions, deletions, status } diff
// on each user message instead, so fall back to combining those turns.
export const GET = withOc(async (_req, { params }: Ctx) => {
  const { id } = await params
  const diff = await ocJson<FileDiff[]>(`/session/${encodeURIComponent(id)}/diff`)
  if (diff?.length) return Response.json(diff)
  const messages = unwrap(await oc().session.messages({ sessionID: id })) ?? []
  return Response.json(aggregateTurnDiffs(messages.map((m) => m.info as MessageInfo)))
})
