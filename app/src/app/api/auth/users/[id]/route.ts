import { withOc } from "@/lib/opencode/server"
import { teamCall } from "@/lib/auth/team"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

type Ctx = { params: Promise<{ id: string }> }

// Disables the person in Cognito and ends all their sessions.
export const DELETE = withOc(async (req, { params }: Ctx) => teamCall(req, "DELETE", `/${encodeURIComponent((await params).id)}`), { role: "owner" })
