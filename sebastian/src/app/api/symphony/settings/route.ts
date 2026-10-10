import { withOc } from "@/lib/opencode/server"
import { readSettings, writeSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = withOc(async () => Response.json(await readSettings()))

export const PUT = withOc(async (req) => Response.json(await writeSettings(await req.json())))
