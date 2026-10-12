import { cognitoConfig, revokeToken } from "@/lib/auth/cognito"
import { COOKIE, cookie, readToken } from "@/lib/auth/session"
import { endSession } from "@/lib/auth/store"
import { withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// Ends the session here and revokes its refresh token at Cognito.
export const POST = withOc(
  async (req) => {
    const refresh = await endSession(readToken(req))
    if (refresh) await revokeToken(cognitoConfig(req), refresh)
    return Response.json({ ok: true }, { headers: { "set-cookie": cookie(COOKIE, "", req, 0) } })
  },
  { public: true },
)
