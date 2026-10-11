import { withOc } from "@/lib/opencode/server"
import { appsOverview } from "@/lib/symphony/apps-server"
import { readSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// GET ?fresh=1 skips the 30-second cache of n8n's workflow list.
export const GET = withOc(async (req) => {
  const fresh = new URL(req.url).searchParams.get("fresh") === "1"
  return Response.json(await appsOverview(await readSettings(), fresh))
})
