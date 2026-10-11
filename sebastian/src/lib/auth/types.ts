// Shared account types (server and browser).

/** The owner set Symphony up and runs it; members are staff they invited. */
export type Role = "owner" | "member"

export type PublicUser = { id: string; name: string; email: string; role: Role; created: number }

/** What GET /api/auth/me returns. */
export type Me =
  | { state: "setup" }
  | { state: "signed-out" }
  | { state: "signed-in"; user: PublicUser; onboarded: boolean }

export type InviteSummary = { id: string; role: Role; created: number; expires: number; by: string }

/** Pages only the owner can open. Members see Today and Work. */
export const OWNER_ONLY_PATHS = ["/coding-agent", "/privacy", "/team"]

/** Pages that work without signing in. */
export const PUBLIC_PATHS = ["/login", "/setup", "/join"]
