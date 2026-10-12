// Shared account types (server and browser).

/** From the Cognito groups: the owner runs Symphony; members are staff. */
export type Role = "owner" | "member"

export type PublicUser = { id: string; name: string; email: string; role: Role; created: number }

/** What GET /api/auth/me returns. */
export type Me =
  | { state: "signed-out" }
  | { state: "signed-in"; user: PublicUser; onboarded: boolean }

/** A person on the team, as the cloud's Team API lists them. */
export type TeamMember = { id: string; email: string; name: string; role: Role; enabled: boolean; signIn: "sso" | "email"; invited: boolean }

/** Pages only the owner can open. Members see Today and Work. */
export const OWNER_ONLY_PATHS = ["/coding-agent", "/privacy", "/team"]

/** Pages that work without signing in. */
export const PUBLIC_PATHS = ["/login"]
