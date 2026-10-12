import { ocJson, withOc } from "@/lib/opencode/server"
import type { RawPermission } from "@/lib/opencode/types"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// Approvals that are already waiting, so a page reload doesn't hide them.
export const GET = withOc(async () => {
  const pending = await ocJson<RawPermission[]>("/permission")
  return Response.json(pending ?? [])
})
