import { teamCall } from "@/lib/auth/team"
import { withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = withOc(async (req) => teamCall(req, "GET"), { role: "owner" })
// { email, name? } -> Cognito sends the person an invitation email.
export const POST = withOc(async (req) => teamCall(req, "POST", "", await req.text()), { role: "owner" })
