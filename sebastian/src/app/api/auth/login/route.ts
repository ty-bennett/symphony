import { sessionCookie } from "@/lib/auth/session"
import { checkLogin, startSession } from "@/lib/auth/store"
import { withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// { email, password } -> session cookie. Five misses in a row lock that email for 30 seconds.
export const POST = withOc(
  async (req) => {
    const body = (await req.json()) as { email?: unknown; password?: unknown }
    const user = await checkLogin(body.email, body.password)
    const token = await startSession(user.id)
    return Response.json({ user }, { headers: { "set-cookie": sessionCookie(token, req) } })
  },
  { public: true },
)
