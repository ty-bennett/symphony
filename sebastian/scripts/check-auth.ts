// Checks for accounts, sessions and invites. Run: npm run check:auth
// Uses a throwaway data folder, never your real .symphony.
import assert from "node:assert/strict"
import { mkdtempSync, statSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

const dir = mkdtempSync(path.join(tmpdir(), "symphony-auth-"))
process.env.SYMPHONY_DATA_DIR = dir

const store = await import("../src/lib/auth/store")
const session = await import("../src/lib/auth/session")

let passed = 0
async function check(name: string, fn: () => Promise<void>) {
  await fn()
  passed++
  console.log(`ok - ${name}`)
}
async function rejects(p: Promise<unknown>, status: number, text?: RegExp) {
  await assert.rejects(p, (e: { status?: number; message?: string }) => {
    assert.equal(e.status, status, e.message ?? "")
    if (text) assert.match(e.message ?? "", text)
    return true
  })
}
const req = (cookie?: string, extra: Record<string, string> = {}) =>
  new Request("http://shop.local:3000/api/x", { headers: { ...(cookie ? { cookie } : {}), ...extra } })

await check("passwords are salted scrypt hashes and verify", async () => {
  const a = await store.hashPassword("correct horse")
  const b = await store.hashPassword("correct horse")
  assert.notEqual(a, b, "salted")
  assert.match(a, /^scrypt\$16384\$/)
  assert.equal(await store.verifyPassword("correct horse", a), true)
  assert.equal(await store.verifyPassword("wrong horse", a), false)
})

await check("first run: Symphony needs setup, then the owner is created once", async () => {
  assert.equal((await session.whoIs(req())).state, "setup")
  await rejects(store.createOwner({ name: "Maria", email: "not-an-email", password: "longenough" }), 400, /valid email/)
  await rejects(store.createOwner({ name: "Maria", email: "maria@bakery.com", password: "short" }), 400, /8 characters/)
  const owner = await store.createOwner({ name: " Maria ", email: "Maria@Bakery.com", password: "flour-and-sugar" })
  assert.deepEqual([owner.name, owner.email, owner.role], ["Maria", "maria@bakery.com", "owner"])
  await rejects(store.createOwner({ name: "Eve", email: "eve@x.com", password: "takeover123" }), 409)
  assert.equal(statSync(path.join(dir, "auth.json")).mode & 0o777, 0o600, "auth file is private")
  assert.equal((await session.whoIs(req())).state, "signed-out")
})

await check("sign in, session cookie, sign out", async () => {
  const user = await store.checkLogin("MARIA@bakery.com ", "flour-and-sugar")
  const token = await store.startSession(user.id)
  const cookie = session.sessionCookie(token, req())
  assert.match(cookie, /HttpOnly; SameSite=Lax; Max-Age=2592000$/)
  assert.doesNotMatch(cookie, /Secure/, "plain http on the shop Wi-Fi")
  assert.match(session.sessionCookie(token, req(undefined, { "x-forwarded-proto": "https" })), /Secure$/)
  const who = await session.whoIs(req(`theme=dark; ${session.COOKIE}=${token}`))
  assert.equal(who.state === "signed-in" && who.user.name, "Maria")
  const raw = JSON.stringify(await import("node:fs/promises").then((f) => f.readFile(path.join(dir, "auth.json"), "utf8")))
  assert.ok(!raw.includes(token), "only a hash of the session token is stored")
  await store.endSession(token)
  assert.equal((await session.whoIs(req(`${session.COOKIE}=${token}`))).state, "signed-out")
  assert.equal((await session.whoIs(req(`${session.COOKIE}=forged`))).state, "signed-out")
})

await check("wrong passwords: same answer for unknown emails, lockout after five", async () => {
  await rejects(store.checkLogin("nobody@bakery.com", "whatever1"), 401, /don't match/)
  for (let i = 0; i < 5; i++) await rejects(store.checkLogin("maria@bakery.com", `wrong-${i}`), 401)
  await rejects(store.checkLogin("maria@bakery.com", "flour-and-sugar"), 429, /Wait 30 seconds/)
})

await check("invites: owner only, one use, member role, revocable", async () => {
  const owner = (await store.listUsers())[0]
  const { code, invite } = await store.createInvite(owner)
  assert.equal(invite.role, "member")
  assert.equal(await store.inviteValid(code), true)
  assert.equal(await store.inviteValid("guess"), false)
  await rejects(store.joinWithInvite(code, { name: "Sam", email: "maria@bakery.com", password: "counter-staff" }), 409, /already uses/)
  const sam = await store.joinWithInvite(code, { name: "Sam", email: "sam@bakery.com", password: "counter-staff" })
  assert.equal(sam.role, "member")
  await rejects(store.joinWithInvite(code, { name: "Eve", email: "eve@x.com", password: "second-use" }), 410, /already used/)
  await rejects(store.createInvite(sam), 403)
  const second = await store.createInvite(owner)
  await store.revokeInvite(second.invite.id)
  assert.equal(await store.inviteValid(second.code), false)
  assert.ok(!(await store.listInvites()).some((i) => i.id === second.invite.id))
})

await check("removing a member signs them out; the owner can't be removed", async () => {
  const [owner, sam] = await store.listUsers()
  const t = await store.startSession(sam.id)
  await rejects(store.removeUser(owner.id, owner), 400)
  await rejects(store.removeUser(owner.id, sam), 400)
  await store.removeUser(sam.id, owner)
  assert.equal((await session.whoIs(req(`${session.COOKIE}=${t}`))).state, "signed-out")
  assert.equal((await store.listUsers()).length, 1)
})

await check("the API wrapper: sign-in required, owner-only routes, same-site changes", async () => {
  const { checkAuth } = await import("../src/lib/opencode/server")
  const owner = (await store.listUsers())[0]
  const { code } = await store.createInvite(owner)
  const member = await store.joinWithInvite(code, { name: "Lee", email: "lee@bakery.com", password: "morning-shift" })
  const ownerCookie = `${session.COOKIE}=${await store.startSession(owner.id)}`
  const memberCookie = `${session.COOKIE}=${await store.startSession(member.id)}`
  assert.equal((await checkAuth(req()))?.status, 401)
  assert.equal(await checkAuth(req(memberCookie)), null)
  assert.equal((await checkAuth(req(memberCookie), "owner"))?.status, 403)
  assert.equal(await checkAuth(req(ownerCookie), "owner"), null)
  const post = (cookie: string, origin: string) =>
    new Request("http://shop.local:3000/api/x", { method: "POST", headers: { cookie, origin, host: "shop.local:3000" } })
  assert.equal(await checkAuth(post(ownerCookie, "http://shop.local:3000")), null)
  assert.equal((await checkAuth(post(ownerCookie, "https://evil.example")))?.status, 403)
})

console.log(`\n${passed} checks passed`)
