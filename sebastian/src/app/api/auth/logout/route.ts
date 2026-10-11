import { clearedCookie, readToken } from "@/lib/auth/session"
import { endSession } from "@/lib/auth/store"
import { withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const POST = withOc(
  async (req) => {
    await endSession(readToken(req))
    return Response.json({ ok: true }, { headers: { "set-cookie": clearedCookie(req) } })
  },
  { public: true },
)
