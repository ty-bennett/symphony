import { cognitoConfig, exchangeCode, LOGIN_COOKIE, profileFrom, readLoginCookie, verifyIdToken } from "@/lib/auth/cognito"
import { COOKIE, cookie } from "@/lib/auth/session"
import { SESSION_DAYS, startSession } from "@/lib/auth/store"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// Cognito sends the browser back here with a code; we trade it for tokens and sign in.
export async function GET(req: Request) {
  const url = new URL(req.url)
  const fail = (why: string) =>
    new Response(null, { status: 302, headers: { location: `/login?error=${encodeURIComponent(why)}`, "set-cookie": cookie(LOGIN_COOKIE, "", req, 0) } })
  if (url.searchParams.get("error")) return fail(url.searchParams.get("error_description") ?? "Sign-in was cancelled.")
  const login = readLoginCookie(req, url.searchParams.get("state"))
  const code = url.searchParams.get("code")
  if (!login || !code) return fail("Sign-in expired. Try again.")
  try {
    const cfg = cognitoConfig(req)
    const tokens = await exchangeCode(cfg, code, login.verifier)
    const profile = profileFrom(await verifyIdToken(cfg, tokens.id_token))
    const session = await startSession(profile, tokens)
    const headers = new Headers({ location: login.next })
    headers.append("set-cookie", cookie(COOKIE, session, req, SESSION_DAYS * 24 * 60 * 60))
    headers.append("set-cookie", cookie(LOGIN_COOKIE, "", req, 0))
    return new Response(null, { status: 302, headers })
  } catch (e) {
    return fail(e instanceof Error ? e.message : "Sign-in failed.")
  }
}
