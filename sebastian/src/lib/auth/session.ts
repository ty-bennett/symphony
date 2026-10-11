// Server-only: the session cookie and "who is asking".
import { OcError } from "@/lib/opencode/errors"
import { hasUsers, SESSION_DAYS, sessionUser } from "./store"
import type { PublicUser } from "./types"

export const COOKIE = "symphony_session"

export function readToken(req: Request): string | null {
  for (const part of (req.headers.get("cookie") ?? "").split(";")) {
    const [k, ...v] = part.trim().split("=")
    if (k === COOKIE) return decodeURIComponent(v.join("="))
  }
  return null
}

// Secure only over HTTPS: the box is often reached as http://<ip> on the shop's Wi-Fi.
const secure = (req: Request) =>
  req.headers.get("x-forwarded-proto") === "https" || new URL(req.url).protocol === "https:" ? "; Secure" : ""

export function sessionCookie(token: string, req: Request): string {
  return `${COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_DAYS * 24 * 60 * 60}${secure(req)}`
}

export function clearedCookie(req: Request): string {
  return `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${secure(req)}`
}

/** A trusted local demo can skip sign-in; everyone is treated as the owner. */
const DEMO_OWNER: PublicUser = { id: "usr_demo", name: "Owner", email: "owner@localhost", role: "owner", created: 0 }
export const authDisabled = () => process.env.OC_ALLOW_UNAUTHENTICATED === "1"

export type Who = { state: "setup" } | { state: "signed-out" } | { state: "signed-in"; user: PublicUser }

export async function whoIs(req: Request): Promise<Who> {
  if (authDisabled()) return { state: "signed-in", user: DEMO_OWNER }
  if (!(await hasUsers())) return { state: "setup" }
  const user = await sessionUser(readToken(req))
  return user ? { state: "signed-in", user } : { state: "signed-out" }
}

/** The signed-in user, or a 401. Use inside handlers that need to know who it is. */
export async function requireUser(req: Request): Promise<PublicUser> {
  const who = await whoIs(req)
  if (who.state !== "signed-in") throw new OcError("Sign in first.", 401)
  return who.user
}
