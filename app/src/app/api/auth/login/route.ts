import { randomBytes } from "node:crypto"
import { authorizeUrl, cognitoConfig, LOGIN_COOKIE, pkce } from "@/lib/auth/cognito"
import { cookie } from "@/lib/auth/session"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// Starts sign-in: off to the Cognito login page (or straight to the company's SSO).
export async function GET(req: Request) {
  const cfg = cognitoConfig(req)
  const { verifier, challenge } = pkce()
  const state = randomBytes(16).toString("base64url")
  const next = new URL(req.url).searchParams.get("next") ?? "/"
  const safe = next.startsWith("/") && !next.startsWith("//") ? next : "/"
  return new Response(null, {
    status: 302,
    headers: {
      location: authorizeUrl(cfg, { state, challenge }),
      "set-cookie": cookie(LOGIN_COOKIE, `${state}.${verifier}.${encodeURIComponent(safe)}`, req, 600),
    },
  })
}
