import { timingSafeEqual } from "node:crypto"
import { cognitoConfig, refreshTokens } from "@/lib/auth/cognito"
import { accessTokenFor } from "@/lib/auth/store"
import { gatewaySecret } from "@/lib/gateway/client"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

const same = (a: string, b: string) => a.length === b.length && timingSafeEqual(Buffer.from(a), Buffer.from(b))

// For the gateway only (shared secret): a fresh Cognito access token for the person
// whose job is running on Orchestra.
export async function GET(req: Request) {
  const secret = gatewaySecret()
  const given = req.headers.get("x-symphony-internal") ?? ""
  if (!secret || !same(given, secret)) return Response.json({ error: "Internal callers only" }, { status: 401 })
  const sub = new URL(req.url).searchParams.get("sub") ?? ""
  const cfg = cognitoConfig(req)
  const token = await accessTokenFor(sub, (r) => refreshTokens(cfg, r)).catch(() => null)
  if (!token) return Response.json({ error: "That person isn't signed in on this box" }, { status: 404 })
  return Response.json(token)
}
