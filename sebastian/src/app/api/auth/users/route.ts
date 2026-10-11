import { listUsers } from "@/lib/auth/store"
import { ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = ownerOnly(async () => Response.json(await listUsers()))
