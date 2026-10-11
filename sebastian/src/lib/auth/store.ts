// Server-only: accounts, sessions and invites, kept in a small JSON file on the
// box (.symphony/auth.json, mode 600). Passwords are hashed with scrypt; session
// tokens and invite codes are stored only as SHA-256 hashes, so the file alone
// can't be used to sign in.
import { createHash, randomBytes, randomUUID, scrypt as scryptCb, timingSafeEqual } from "node:crypto"
import { mkdir, readFile, rename, writeFile } from "node:fs/promises"
import path from "node:path"
import { promisify } from "node:util"
import { OcError } from "@/lib/opencode/errors"
import { dataDir } from "@/lib/symphony/settings-store"
import type { InviteSummary, PublicUser, Role } from "./types"

const scrypt = promisify(scryptCb) as (pw: string, salt: Buffer, len: number, opts: { N: number; r: number; p: number }) => Promise<Buffer>

type StoredUser = PublicUser & { hash: string }
type StoredInvite = { id: string; codeHash: string; role: Role; created: number; expires: number; by: string }
type StoredSession = { idHash: string; userId: string; created: number; expires: number }
type Store = { users: StoredUser[]; invites: StoredInvite[]; sessions: StoredSession[] }

export const SESSION_DAYS = 30
export const INVITE_DAYS = 7
const DAY = 24 * 60 * 60 * 1000

const file = () => path.join(dataDir(), "auth.json")
const sha = (s: string) => createHash("sha256").update(s).digest("hex")
const token = () => randomBytes(32).toString("base64url")

async function readStore(): Promise<Store> {
  try {
    const s = JSON.parse(await readFile(file(), "utf8")) as Partial<Store>
    return { users: s.users ?? [], invites: s.invites ?? [], sessions: s.sessions ?? [] }
  } catch {
    return { users: [], invites: [], sessions: [] }
  }
}

async function writeStore(store: Store) {
  const now = Date.now()
  store.sessions = store.sessions.filter((s) => s.expires > now)
  store.invites = store.invites.filter((i) => i.expires > now)
  await mkdir(dataDir(), { recursive: true })
  const tmp = `${file()}.${process.pid}.${randomUUID()}.tmp`
  await writeFile(tmp, JSON.stringify(store, null, 2), { mode: 0o600 })
  await rename(tmp, file())
}

// One change at a time, so two requests can't overwrite each other.
let queue: Promise<unknown> = Promise.resolve()
function change<T>(fn: (store: Store) => Promise<T> | T): Promise<T> {
  const run = queue.then(async () => {
    const store = await readStore()
    const result = await fn(store)
    await writeStore(store)
    return result
  })
  queue = run.catch(() => undefined)
  return run
}

const publicUser = ({ id, name, email, role, created }: StoredUser): PublicUser => ({ id, name, email, role, created })

// ---------- passwords ----------

const N = 16384
export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16)
  const key = await scrypt(password, salt, 64, { N, r: 8, p: 1 })
  return `scrypt$${N}$${salt.toString("base64url")}$${key.toString("base64url")}`
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [kind, n, salt, hash] = stored.split("$")
  if (kind !== "scrypt" || !salt || !hash) return false
  const expected = Buffer.from(hash, "base64url")
  const key = await scrypt(password, Buffer.from(salt, "base64url"), expected.length, { N: Number(n), r: 8, p: 1 })
  return key.length === expected.length && timingSafeEqual(key, expected)
}

// A real hash to compare against when the email is unknown, so timing doesn't reveal accounts.
const dummy = hashPassword("not-a-real-password")

// ---------- validation ----------

export function cleanAccount(input: { name?: unknown; email?: unknown; password?: unknown }) {
  const name = typeof input.name === "string" ? input.name.trim().slice(0, 60) : ""
  const email = typeof input.email === "string" ? input.email.trim().toLowerCase().slice(0, 120) : ""
  const password = typeof input.password === "string" ? input.password : ""
  if (!name) throw new OcError("Enter your name.", 400)
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new OcError("Enter a valid email address.", 400)
  if (password.length < 8) throw new OcError("Use a password of at least 8 characters.", 400)
  if (password.length > 200) throw new OcError("That password is too long.", 400)
  return { name, email, password }
}

// ---------- users ----------

export async function hasUsers() {
  return (await readStore()).users.length > 0
}

export async function listUsers(): Promise<PublicUser[]> {
  return (await readStore()).users.map(publicUser)
}

/** First run only: the person setting Symphony up becomes the owner. */
export async function createOwner(input: { name?: unknown; email?: unknown; password?: unknown }): Promise<PublicUser> {
  const account = cleanAccount(input)
  const hash = await hashPassword(account.password)
  return change((store) => {
    if (store.users.length) throw new OcError("Symphony is already set up. Sign in instead.", 409)
    const user: StoredUser = { id: `usr_${randomUUID()}`, name: account.name, email: account.email, role: "owner", created: Date.now(), hash }
    store.users.push(user)
    return publicUser(user)
  })
}

export async function removeUser(id: string, by: PublicUser) {
  await change((store) => {
    const user = store.users.find((u) => u.id === id)
    if (!user) throw new OcError("That person isn't on the team.", 404)
    if (user.role === "owner") throw new OcError("The owner can't be removed.", 400)
    if (by.role !== "owner") throw new OcError("Only the owner can do that.", 403)
    store.users = store.users.filter((u) => u.id !== id)
    store.sessions = store.sessions.filter((s) => s.userId !== id)
  })
}

// ---------- signing in ----------

// Slow down password guessing: after 5 misses for an email, wait 30 seconds.
const misses = new Map<string, { count: number; until: number }>()

export async function checkLogin(emailInput: unknown, password: unknown): Promise<PublicUser> {
  const email = typeof emailInput === "string" ? emailInput.trim().toLowerCase() : ""
  const pw = typeof password === "string" ? password : ""
  const m = misses.get(email)
  if (m && m.until > Date.now()) throw new OcError("Too many tries. Wait 30 seconds and try again.", 429)
  const user = (await readStore()).users.find((u) => u.email === email)
  const ok = await verifyPassword(pw, user?.hash ?? (await dummy))
  if (!user || !ok) {
    const count = (m?.count ?? 0) + 1
    misses.set(email, { count, until: count >= 5 ? Date.now() + 30_000 : 0 })
    throw new OcError("That email and password don't match.", 401)
  }
  misses.delete(email)
  return publicUser(user)
}

export async function startSession(userId: string): Promise<string> {
  const t = token()
  await change((store) => {
    store.sessions.push({ idHash: sha(t), userId, created: Date.now(), expires: Date.now() + SESSION_DAYS * DAY })
  })
  return t
}

export async function sessionUser(sessionToken: string | null): Promise<PublicUser | null> {
  if (!sessionToken) return null
  const store = await readStore()
  const s = store.sessions.find((x) => x.idHash === sha(sessionToken) && x.expires > Date.now())
  const user = s && store.users.find((u) => u.id === s.userId)
  return user ? publicUser(user) : null
}

export async function endSession(sessionToken: string | null) {
  if (!sessionToken) return
  await change((store) => {
    store.sessions = store.sessions.filter((s) => s.idHash !== sha(sessionToken))
  })
}

// ---------- invites ----------

export async function createInvite(by: PublicUser): Promise<{ invite: InviteSummary; code: string }> {
  if (by.role !== "owner") throw new OcError("Only the owner can do that.", 403)
  const code = token()
  const invite: StoredInvite = {
    id: `inv_${randomUUID()}`,
    codeHash: sha(code),
    role: "member",
    created: Date.now(),
    expires: Date.now() + INVITE_DAYS * DAY,
    by: by.id,
  }
  await change((store) => {
    store.invites.push(invite)
  })
  const { codeHash: _, ...summary } = invite
  void _
  return { invite: summary, code }
}

export async function listInvites(): Promise<InviteSummary[]> {
  return (await readStore()).invites
    .filter((i) => i.expires > Date.now())
    .map(({ codeHash: _, ...i }) => {
      void _
      return i
    })
}

export async function revokeInvite(id: string) {
  await change((store) => {
    store.invites = store.invites.filter((i) => i.id !== id)
  })
}

/** Is this invite code still good? (For the join page, before the form is filled.) */
export async function inviteValid(code: unknown): Promise<boolean> {
  if (typeof code !== "string" || !code) return false
  return (await readStore()).invites.some((i) => i.codeHash === sha(code) && i.expires > Date.now())
}

/** Use an invite once: creates the member's account. */
export async function joinWithInvite(code: unknown, input: { name?: unknown; email?: unknown; password?: unknown }): Promise<PublicUser> {
  const account = cleanAccount(input)
  const hash = await hashPassword(account.password)
  return change((store) => {
    const invite = typeof code === "string" ? store.invites.find((i) => i.codeHash === sha(code) && i.expires > Date.now()) : undefined
    if (!invite) throw new OcError("This invite link has expired or was already used. Ask for a new one.", 410)
    if (store.users.some((u) => u.email === account.email)) throw new OcError("Someone on the team already uses that email.", 409)
    const user: StoredUser = { id: `usr_${randomUUID()}`, name: account.name, email: account.email, role: invite.role, created: Date.now(), hash }
    store.users.push(user)
    store.invites = store.invites.filter((i) => i !== invite)
    return publicUser(user)
  })
}
