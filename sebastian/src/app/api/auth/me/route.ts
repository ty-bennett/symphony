import { whoIs } from "@/lib/auth/session"
import type { Me } from "@/lib/auth/types"
import { withOc } from "@/lib/opencode/server"
import { readSettings } from "@/lib/symphony/settings-store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// Who is signed in, or whether Symphony still needs its first-run setup.
export const GET = withOc(
  async (req) => {
    const who = await whoIs(req)
    const me: Me = who.state === "signed-in" ? { ...who, onboarded: (await readSettings()).onboarded } : who
    return Response.json(me)
  },
  { public: true },
)
