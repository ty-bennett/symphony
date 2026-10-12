// Server-only: sign-in sessions, kept in a small JSON file on the box
// (.symphony/sessions.json, mode 600). Accounts live in Cognito; this file holds only
// sessions. Session cookies are stored as SHA-256 hashes, and each person's Cognito
// refresh token is encrypted with a key kept in .symphony/session.key (mode 600).
import { createCipheriv, createDecipheriv, createHash, randomBytes, randomUUID } from "node:crypto"
import { mkdir, readFile, rename, writeFile } from "node:fs/promises"
import path from "node:path"
import { dataDir } from "@/lib/symphony/settings-store"
import type { Profile, Tokens } from "./cognito"
import type { PublicUser } from "./types"

type StoredSession = {
  idHash: string
  user: PublicUser
  refresh: string
  access: string
  accessExpires: number
  created: number
  expires: number
}
type Store = { sessions: StoredSession[]; people: Record<string, PublicUser> }

export const SESSION_DAYS = 30
const DAY = 24 * 60 * 60 * 1000

const file = () => path.join(dataDir(), "sessions.json")
const sha = (s: string) => createHash("sha256").update(s).digest("hex")

async function key(): Promise<Buffer> {
  const keyFile = path.join(dataDir(), "session.key")
  try {
    return Buffer.from((await readFile(keyFile, "utf8")).trim(), "base64")
  } catch {
    const k = randomBytes(32)
    await mkdir(dataDir(), { recursive: true })
    await writeFile(keyFile, k.toString("base64"), { mode: 0o600, flag: "wx" }).catch(() => undefined)
    return Buffer.from((await readFile(keyFile, "utf8")).trim(), "base64")
  }
}

async function seal(plain: string) {
  const iv = randomBytes(12)
  const cipher = createCipheriv("aes-256-gcm", await key(), iv)
  const body = Buffer.concat([cipher.update(plain, "utf8"), cipher.final()])
  return Buffer.concat([iv, cipher.getAuthTag(), body]).toString("base64")
}

async function open(sealed: string) {
  const raw = Buffer.from(sealed, "base64")
  const decipher = createDecipheriv("aes-256-gcm", await key(), raw.subarray(0, 12))
  decipher.setAuthTag(raw.subarray(12, 28))
  return Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]).toString("utf8")
}

async function readStore(): Promise<Store> {
  try {
    const s = JSON.parse(await readFile(file(), "utf8")) as Partial<Store>
    return { sessions: s.sessions ?? [], people: s.people ?? {} }
  } catch {
    return { sessions: [], people: {} }
  }
}

async function writeStore(store: Store) {
  store.sessions = store.sessions.filter((s) => s.expires > Date.now())
  await mkdir(dataDir(), { recursive: true })
  const tmp = `${file()}.${process.pid}.${randomUUID()}.tmp`
  await writeFile(tmp, JSON.stringify(store, null, 2), { mode: 0o600 })
  await rename(tmp, file())
}

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

export async function startSession(profile: Profile, tokens: Pick<Tokens, "access_token" | "refresh_token" | "expires_in">): Promise<string> {
  const token = randomBytes(32).toString("base64url")
  const now = Date.now()
  const refresh = await seal(tokens.refresh_token)
  await change((store) => {
    const user: PublicUser = { ...profile, created: store.people[profile.id]?.created ?? now }
    store.people[profile.id] = user
    store.sessions.push({ idHash: sha(token), user, refresh, access: tokens.access_token, accessExpires: now + tokens.expires_in * 1000, created: now, expires: now + SESSION_DAYS * DAY })
  })
  return token
}

export async function sessionUser(sessionToken: string | null): Promise<PublicUser | null> {
  if (!sessionToken) return null
  const s = (await readStore()).sessions.find((x) => x.idHash === sha(sessionToken) && x.expires > Date.now())
  return s?.user ?? null
}

/** Ends the session and returns its refresh token so it can be revoked at Cognito. */
export async function endSession(sessionToken: string | null): Promise<string | null> {
  if (!sessionToken) return null
  const sealed = await change((store) => {
    const s = store.sessions.find((x) => x.idHash === sha(sessionToken))
    store.sessions = store.sessions.filter((x) => x.idHash !== sha(sessionToken))
    return s?.refresh ?? null
  })
  return sealed ? open(sealed) : null
}

/**
 * A fresh access token for a person, from their newest session (refreshed when it
 * expires in under a minute). Used by the gateway for Orchestra and by Team calls.
 */
export async function accessTokenFor(
  sub: string,
  refresh: (refreshToken: string) => Promise<{ access_token: string; expires_in: number }>,
): Promise<{ access_token: string; expires_in: number } | null> {
  const store = await readStore()
  const s = store.sessions.filter((x) => x.user.id === sub && x.expires > Date.now()).sort((a, b) => b.created - a.created)[0]
  if (!s) return null
  if (s.accessExpires - 60_000 > Date.now()) return { access_token: s.access, expires_in: Math.floor((s.accessExpires - Date.now()) / 1000) }
  const fresh = await refresh(await open(s.refresh))
  await change((st) => {
    const target = st.sessions.find((x) => x.idHash === s.idHash)
    if (target) {
      target.access = fresh.access_token
      target.accessExpires = Date.now() + fresh.expires_in * 1000
    }
  })
  return fresh
}

/** The access token behind a browser session (for calls made on the person's behalf). */
export async function accessTokenForSession(
  sessionToken: string | null,
  refresh: (refreshToken: string) => Promise<{ access_token: string; expires_in: number }>,
) {
  const user = await sessionUser(sessionToken)
  return user ? accessTokenFor(user.id, refresh) : null
}

/** Everyone who has signed in on this box (names for job history). */
export async function knownPeople(): Promise<PublicUser[]> {
  return Object.values((await readStore()).people)
}
