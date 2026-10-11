import { requireUser } from "@/lib/auth/session"
import { createInvite, listInvites } from "@/lib/auth/store"
import { ownerOnly } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = ownerOnly(async () => Response.json(await listInvites()))

// -> { invite, code }. The page turns the code into a link: <origin>/join?code=...
// The code is shown once; only its hash is stored.
export const POST = ownerOnly(async (req) => Response.json(await createInvite(await requireUser(req)), { status: 201 }))
