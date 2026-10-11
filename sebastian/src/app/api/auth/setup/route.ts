import { sessionCookie } from "@/lib/auth/session"
import { createOwner, startSession } from "@/lib/auth/store"
import { withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// First run only: { name, email, password } creates the owner and signs them in.
export const POST = withOc(
  async (req) => {
    const user = await createOwner(await req.json())
    const token = await startSession(user.id)
    return Response.json({ user }, { status: 201, headers: { "set-cookie": sessionCookie(token, req) } })
  },
  { public: true },
)
