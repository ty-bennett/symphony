import { sessionCookie } from "@/lib/auth/session"
import { inviteValid, joinWithInvite, startSession } from "@/lib/auth/store"
import { withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// GET ?code= -> { valid }: lets the join page say early if a link is dead.
export const GET = withOc(async (req) => Response.json({ valid: await inviteValid(new URL(req.url).searchParams.get("code")) }), {
  public: true,
})

// { code, name, email, password } -> a member account, signed in. Each invite works once.
export const POST = withOc(
  async (req) => {
    const body = (await req.json()) as { code?: unknown; name?: unknown; email?: unknown; password?: unknown }
    const user = await joinWithInvite(body.code, body)
    const token = await startSession(user.id)
    return Response.json({ user }, { status: 201, headers: { "set-cookie": sessionCookie(token, req) } })
  },
  { public: true },
)
