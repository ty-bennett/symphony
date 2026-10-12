// Server-only: the session cookie and "who is asking".
import { OcError } from "@/lib/opencode/errors"
import { sessionUser } from "./store"
import type { PublicUser } from "./types"

export const COOKIE = "symphony_session"

export function readCookie(req: Request, name: string): string | null {
  for (const part of (req.headers.get("cookie") ?? "").split(";")) {
    const [k, ...v] = part.trim().split("=")
    if (k === name) return decodeURIComponent(v.join("="))
  }
  return null
}

export const readToken = (req: Request) => readCookie(req, COOKIE)

// Secure only over HTTPS: the box is often reached as http://<ip> on the shop's Wi-Fi.
const secure = (req: Request) =>
  req.headers.get("x-forwarded-proto") === "https" || new URL(req.url).protocol === "https:" ? "; Secure" : ""

export function cookie(name: string, value: string, req: Request, maxAgeSeconds: number): string {
  return `${name}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAgeSeconds}${secure(req)}`
}

/** A trusted local demo can skip sign-in; everyone is treated as the owner. */
const DEMO_OWNER: PublicUser = { id: "usr_demo", name: "Owner", email: "owner@localhost", role: "owner", created: 0 }
export const authDisabled = () => process.env.OC_ALLOW_UNAUTHENTICATED === "1"

export type Who = { state: "signed-out" } | { state: "signed-in"; user: PublicUser }

export async function whoIs(req: Request): Promise<Who> {
  if (authDisabled()) return { state: "signed-in", user: DEMO_OWNER }
  const user = await sessionUser(readToken(req))
  return user ? { state: "signed-in", user } : { state: "signed-out" }
}

/** The signed-in user, or a 401. Use inside handlers that need to know who it is. */
export async function requireUser(req: Request): Promise<PublicUser> {
  const who = await whoIs(req)
  if (who.state !== "signed-in") throw new OcError("Sign in first.", 401)
  return who.user
}
