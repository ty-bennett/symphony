// Team management for the Symphony app, backed by the tenant's Cognito pool.
// Owner-only; the box never holds AWS credentials. Injected deps keep it testable.
import { createHash } from "node:crypto"

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const hash = (value) => createHash("sha256").update(value).digest("hex").slice(0, 16)
const json = (status, value) => ({ status, body: value === undefined ? "" : JSON.stringify(value) })
const fail = (status, code, message) => json(status, { code, message })

/** deps: { listUsers(), createUser({email, name}) -> {sub}, addToGroup(sub, group), disableUser(sub), signOut(sub), log(line) } */
export async function handleAdmin({ method, path, body, auth, deps, config }) {
  const audit = { tenant: auth.tenantId, user: auth.userSub ? hash(auth.userSub) : undefined, method, route: path?.replace(/\/[^/]+$/, "/:id") }
  const groups = (auth.groups ?? "").split(",").filter(Boolean)
  try {
    if (auth.tenantId !== config.tenantId || !groups.includes("owner")) {
      audit.status = "OWNER_ONLY"
      return fail(403, "OWNER_ONLY", "Only the owner can manage the team")
    }
    const match = /^\/v1\/admin\/users(?:\/([^/]+))?$/.exec(path ?? "")
    if (!match) return (audit.status = "NOT_FOUND"), fail(404, "NOT_FOUND", "Unknown route")
    const target = match[1] ? decodeURIComponent(match[1]) : undefined

    if (method === "GET" && !target) {
      const users = (await deps.listUsers()).map((u) => ({
        id: u.sub,
        email: u.email,
        name: u.name || u.email,
        role: u.groups.includes("owner") ? "owner" : "member",
        enabled: u.enabled,
        signIn: u.status === "EXTERNAL_PROVIDER" ? "sso" : "email",
        invited: u.status === "FORCE_CHANGE_PASSWORD",
      }))
      audit.status = "ok"
      return json(200, { users })
    }
    if (method === "POST" && !target) {
      let input
      try {
        input = JSON.parse(body ?? "{}")
      } catch {
        input = {}
      }
      const email = typeof input.email === "string" ? input.email.trim().toLowerCase() : ""
      if (!EMAIL.test(email)) return (audit.status = "INVALID_REQUEST"), fail(400, "INVALID_REQUEST", "Enter a valid email address")
      const name = typeof input.name === "string" ? input.name.trim().slice(0, 80) : ""
      const created = await deps.createUser({ email, name })
      await deps.addToGroup(created.sub, "member")
      audit.status = "ok"
      return json(201, { id: created.sub, email, name: name || email, role: "member", invited: true })
    }
    if (method === "DELETE" && target) {
      if (target === auth.userSub) return (audit.status = "INVALID_REQUEST"), fail(400, "INVALID_REQUEST", "The owner can't remove themselves")
      await deps.disableUser(target)
      await deps.signOut(target)
      audit.status = "ok"
      return json(204)
    }
    audit.status = "NOT_FOUND"
    return fail(404, "NOT_FOUND", "Unknown route")
  } catch (error) {
    if (error?.name === "UsernameExistsException") return (audit.status = "EXISTS"), fail(409, "EXISTS", "That person already has an account")
    if (error?.name === "UserNotFoundException") return (audit.status = "NOT_FOUND"), fail(404, "NOT_FOUND", "No such person")
    audit.status = "INTERNAL"
    return fail(500, "INTERNAL", "Request failed")
  } finally {
    deps.log(audit)
  }
}
