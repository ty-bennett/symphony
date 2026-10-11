import { test } from "node:test"
import assert from "node:assert/strict"
import { handleAdmin } from "../src/admin.mjs"

const owner = { tenantId: "demo", userSub: "sub-owner", groups: "owner" }
const member = { tenantId: "demo", userSub: "sub-member", groups: "member" }
const config = { tenantId: "demo" }

function deps(over = {}) {
  const calls = []
  return {
    calls,
    listUsers: async () => [
      { sub: "sub-owner", email: "owner@example.com", name: "Olive", enabled: true, status: "CONFIRMED", groups: ["owner"] },
      { sub: "sub-member", email: "m@example.com", name: "", enabled: true, status: "EXTERNAL_PROVIDER", groups: [] },
    ],
    createUser: async (u) => (calls.push(["create", u.email, u.name]), { sub: "sub-new" }),
    addToGroup: async (sub, group) => calls.push(["group", sub, group]),
    disableUser: async (sub) => calls.push(["disable", sub]),
    signOut: async (sub) => calls.push(["signout", sub]),
    log: (line) => calls.push(["log", line]),
    ...over,
  }
}

const call = (d, method, path, auth = owner, body) => handleAdmin({ method, path, body: body ? JSON.stringify(body) : undefined, auth, deps: d, config })

test("lists the team with roles from groups (no group means member)", async () => {
  const r = await call(deps(), "GET", "/v1/admin/users")
  assert.equal(r.status, 200)
  assert.deepEqual(
    JSON.parse(r.body).users.map((u) => [u.id, u.role, u.signIn]),
    [
      ["sub-owner", "owner", "email"],
      ["sub-member", "member", "sso"],
    ],
  )
})

test("invites a member: Cognito user with an invitation email, then the member group", async () => {
  const d = deps()
  const r = await call(d, "POST", "/v1/admin/users", owner, { email: "New.Person@Example.com", name: "New Person" })
  assert.equal(r.status, 201)
  assert.deepEqual(d.calls.filter((c) => c[0] !== "log"), [
    ["create", "new.person@example.com", "New Person"],
    ["group", "sub-new", "member"],
  ])
})

test("removing someone disables them and ends every session; the owner can't remove themselves", async () => {
  const d = deps()
  assert.equal((await call(d, "DELETE", "/v1/admin/users/sub-member")).status, 204)
  assert.deepEqual(d.calls.filter((c) => c[0] !== "log"), [["disable", "sub-member"], ["signout", "sub-member"]])
  assert.equal((await call(deps(), "DELETE", "/v1/admin/users/sub-owner")).status, 400)
})

test("only owners of this tenant may use it", async () => {
  for (const auth of [member, { ...owner, groups: "" }, { ...owner, tenantId: "other" }]) {
    const d = deps()
    const r = await call(d, "GET", "/v1/admin/users", auth)
    assert.equal(r.status, 403)
    assert.equal(JSON.parse(r.body).code, "OWNER_ONLY")
    assert.equal(d.calls.filter((c) => c[0] !== "log").length, 0)
  }
})

test("rejects bad input and unknown routes; logs carry no emails", async () => {
  const d = deps()
  assert.equal((await call(d, "POST", "/v1/admin/users", owner, { email: "not-an-email" })).status, 400)
  assert.equal((await call(d, "PUT", "/v1/admin/users", owner)).status, 404)
  const exists = deps({ createUser: async () => { const e = new Error("x"); e.name = "UsernameExistsException"; throw e } })
  assert.equal((await call(exists, "POST", "/v1/admin/users", owner, { email: "a@b.co" })).status, 409)
  const logs = JSON.stringify([...d.calls, ...exists.calls].filter((c) => c[0] === "log"))
  assert.ok(!logs.includes("@"), "no email addresses in audit lines")
})
