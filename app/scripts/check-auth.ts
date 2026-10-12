// Checks for Cognito sign-in, sessions and the gateway token hand-off. Run: npm run check:auth
// Uses a throwaway data folder, never your real .symphony.
import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const dir = mkdtempSync(path.join(tmpdir(), "symphony-auth-"))
process.env.SYMPHONY_DATA_DIR = dir
process.env.COGNITO_DOMAIN = "https://symphony-demo.auth.us-east-1.amazoncognito.com"
process.env.COGNITO_CLIENT_ID = "client123"
process.env.COGNITO_USER_POOL_ID = "us-east-1_Pool1"
const secretFile = path.join(dir, "internal.secret")
writeFileSync(secretFile, "gateway-secret\n")
process.env.SYMPHONY_GATEWAY_SECRET_FILE = secretFile

const cognito = await import("../src/lib/auth/cognito")
const store = await import("../src/lib/auth/store")
const session = await import("../src/lib/auth/session")
const { checkAuth } = await import("../src/lib/opencode/server")
const internalToken = await import("../src/app/api/internal/token/route")

let passed = 0
async function check(name: string, fn: () => Promise<void> | void) {
  await fn()
  passed++
  console.log(`ok - ${name}`)
}
const req = (cookie?: string, extra: Record<string, string> = {}, url = "http://shop.local:3000/api/x") =>
  new Request(url, { headers: { ...(cookie ? { cookie } : {}), ...extra } })
const b64url = (b: Buffer) => b.toString("base64url")
const cfg = cognito.cognitoConfig(req())

await check("PKCE: the challenge is the S256 hash of the verifier", () => {
  const { verifier, challenge } = cognito.pkce()
  assert.ok(verifier.length >= 43)
  assert.equal(challenge, b64url(createHash("sha256").update(verifier).digest()))
})

await check("the login link uses the code flow with PKCE, and goes straight to SSO when one provider is set", () => {
  const url = new URL(cognito.authorizeUrl(cfg, { state: "s1", challenge: "c1" }))
  assert.equal(url.origin + url.pathname, "https://symphony-demo.auth.us-east-1.amazoncognito.com/oauth2/authorize")
  assert.equal(url.searchParams.get("response_type"), "code")
  assert.equal(url.searchParams.get("code_challenge_method"), "S256")
  assert.equal(url.searchParams.get("redirect_uri"), "http://shop.local:3000/api/auth/callback")
  assert.equal(url.searchParams.get("identity_provider"), null)
  const sso = new URL(cognito.authorizeUrl({ ...cfg, identityProvider: "Google" }, { state: "s1", challenge: "c1" }))
  assert.equal(sso.searchParams.get("identity_provider"), "Google")
})

await check("the callback refuses a state that doesn't match the login cookie", () => {
  assert.equal(cognito.readLoginCookie(req("symphony_login=abc.verifier.%2F"), "abc")?.verifier, "verifier")
  assert.equal(cognito.readLoginCookie(req("symphony_login=abc.verifier.%2F"), "other"), null)
  assert.equal(cognito.readLoginCookie(req(), "abc"), null)
})

await check("roles come from Cognito groups; no group means member", () => {
  assert.equal(cognito.profileFrom({ sub: "u1", email: "o@x.co", "cognito:groups": ["owner"] }).role, "owner")
  const m = cognito.profileFrom({ sub: "u2", email: "m@x.co", name: "Mia" })
  assert.equal(m.role, "member")
  assert.equal(m.name, "Mia")
  assert.equal(cognito.profileFrom({ sub: "u3", email: "z@x.co" }).name, "z@x.co")
})

const tokens = (access: string, refresh = "refresh-secret-1") => ({ id_token: "id", access_token: access, refresh_token: refresh, expires_in: 3600 })

await check("a session keeps the refresh token encrypted on disk, in a 600 file", async () => {
  const t = await store.startSession({ id: "u1", email: "o@x.co", name: "Olive", role: "owner" }, tokens("access-1"))
  const raw = readFileSync(path.join(dir, "sessions.json"), "utf8")
  assert.ok(!raw.includes("refresh-secret-1"), "refresh token must not be stored in plain text")
  assert.ok(!raw.includes(t), "session token must not be stored in plain text")
  assert.equal(statSync(path.join(dir, "sessions.json")).mode & 0o777, 0o600)
  const who = await session.whoIs(req(`symphony_session=${t}`))
  assert.equal(who.state === "signed-in" && who.user.role, "owner")
  assert.equal((await session.whoIs(req())).state, "signed-out")
})

await check("the gateway gets a fresh access token for a person, refreshing it when it has expired", async () => {
  const t = await store.startSession({ id: "u9", email: "r@x.co", name: "Ray", role: "member" }, { ...tokens("old-access", "refresh-9"), expires_in: 30 })
  let refreshed = 0
  const fresh = await store.accessTokenFor("u9", async (refreshToken) => {
    refreshed++
    assert.equal(refreshToken, "refresh-9")
    return { access_token: "new-access", expires_in: 3600 }
  })
  assert.equal(fresh?.access_token, "new-access")
  assert.equal(refreshed, 1)
  assert.equal((await store.accessTokenFor("u9", async () => assert.fail("should be cached")))?.access_token, "new-access")
  assert.equal(await store.accessTokenFor("nobody", async () => assert.fail("no session")), null)
  assert.equal(await store.endSession(t), "refresh-9", "sign-out returns the refresh token so it can be revoked")
  assert.equal(await store.accessTokenFor("u9", async () => assert.fail("signed out")), null)
})

await check("the internal token route needs the gateway's secret", async () => {
  await store.startSession({ id: "u5", email: "a@x.co", name: "A", role: "member" }, tokens("access-5"))
  const denied = await internalToken.GET(req(undefined, {}, "http://127.0.0.1:3000/api/internal/token?sub=u5"))
  assert.equal(denied.status, 401)
  const ok = await internalToken.GET(req(undefined, { "x-symphony-internal": "gateway-secret" }, "http://127.0.0.1:3000/api/internal/token?sub=u5"))
  assert.equal(ok.status, 200)
  assert.equal(((await ok.json()) as { access_token: string }).access_token, "access-5")
  const missing = await internalToken.GET(req(undefined, { "x-symphony-internal": "gateway-secret" }, "http://127.0.0.1:3000/api/internal/token?sub=gone"))
  assert.equal(missing.status, 404)
})

await check("the API wrapper: sign-in required, owner-only routes, same-site changes", async () => {
  const owner = await store.startSession({ id: "o1", email: "o@x.co", name: "O", role: "owner" }, tokens("a"))
  const member = await store.startSession({ id: "m1", email: "m@x.co", name: "M", role: "member" }, tokens("b"))
  assert.equal((await checkAuth(req()))?.status, 401)
  assert.equal(await checkAuth(req(`symphony_session=${member}`)), null)
  assert.equal((await checkAuth(req(`symphony_session=${member}`), "owner"))?.status, 403)
  assert.equal(await checkAuth(req(`symphony_session=${owner}`), "owner"), null)
  const cross = new Request("http://shop.local:3000/api/x", { method: "POST", headers: { cookie: `symphony_session=${owner}`, origin: "https://evil.example", host: "shop.local:3000" } })
  assert.equal((await checkAuth(cross))?.status, 403)
})

console.log(`\n${passed} checks passed`)
