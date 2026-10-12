import { ocJson, ownerOnly } from "@/lib/opencode/server"
import type { Todo } from "@/lib/opencode/types"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

export const GET = ownerOnly(async (_req, { params }: Ctx) => {
  const { id } = await params
  const todos = await ocJson<Todo[]>(`/session/${encodeURIComponent(id)}/todo`)
  return Response.json(todos ?? [])
})
