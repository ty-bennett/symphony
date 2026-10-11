import { ownerOnly, withOc } from "@/lib/opencode/server"
import { readSettings, writeSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = withOc(async () => Response.json(await readSettings()))

// Fields left out keep their saved values (so an older screen can't undo "onboarded").
export const PUT = ownerOnly(async (req) => {
  const body = (await req.json()) as Record<string, unknown>
  return Response.json(await writeSettings({ ...(await readSettings()), ...(body && typeof body === "object" ? body : {}) }))
})
