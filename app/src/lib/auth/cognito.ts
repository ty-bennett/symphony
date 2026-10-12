// Server-only: sign-in through the tenant's Cognito hosted login (and the company's
// SSO behind it), using the authorization code flow with PKCE. No passwords on the box.
import { createHash, randomBytes } from "node:crypto"
import { CognitoJwtVerifier } from "aws-jwt-verify"
import { OcError } from "@/lib/opencode/errors"
import type { Role } from "./types"

export type CognitoConfig = {
  domain: string
  clientId: string
  userPoolId: string
  redirectUri: string
  identityProvider?: string
}

export type Tokens = { id_token: string; access_token: string; refresh_token: string; expires_in: number }
export type Profile = { id: string; email: string; name: string; role: Role }

export const LOGIN_COOKIE = "symphony_login"

/** From env (COGNITO_DOMAIN, COGNITO_CLIENT_ID, COGNITO_USER_POOL_ID, optional COGNITO_IDENTITY_PROVIDER, SYMPHONY_PUBLIC_URL). */
export function cognitoConfig(req: Request): CognitoConfig {
  const domain = process.env.COGNITO_DOMAIN?.replace(/\/+$/, "")
  const clientId = process.env.COGNITO_CLIENT_ID
  const userPoolId = process.env.COGNITO_USER_POOL_ID
  if (!domain || !clientId || !userPoolId) throw new OcError("Sign-in isn't configured: set COGNITO_DOMAIN, COGNITO_CLIENT_ID and COGNITO_USER_POOL_ID.", 500)
  const origin = (process.env.SYMPHONY_PUBLIC_URL ?? new URL(req.url).origin).replace(/\/+$/, "")
  return { domain, clientId, userPoolId, redirectUri: `${origin}/api/auth/callback`, identityProvider: process.env.COGNITO_IDENTITY_PROVIDER || undefined }
}

export function pkce() {
  const verifier = randomBytes(32).toString("base64url")
  return { verifier, challenge: createHash("sha256").update(verifier).digest("base64url") }
}

export function authorizeUrl(cfg: CognitoConfig, { state, challenge }: { state: string; challenge: string }) {
  const url = new URL(`${cfg.domain}/oauth2/authorize`)
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: cfg.redirectUri,
    scope: "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    ...(cfg.identityProvider ? { identity_provider: cfg.identityProvider } : {}),
  }).toString()
  return url.toString()
}

/** The login cookie holds state.verifier.next; returns it only if state matches. */
export function readLoginCookie(req: Request, state: string | null): { verifier: string; next: string } | null {
  for (const part of (req.headers.get("cookie") ?? "").split(";")) {
    const [k, ...v] = part.trim().split("=")
    if (k !== LOGIN_COOKIE) continue
    const [s, verifier, next] = v.join("=").split(".")
    if (!state || s !== state || !verifier) return null
    return { verifier, next: decodeURIComponent(next ?? "%2F") }
  }
  return null
}

async function tokenRequest(cfg: CognitoConfig, params: Record<string, string>, fetchImpl: typeof fetch) {
  const response = await fetchImpl(`${cfg.domain}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: cfg.clientId, ...params }).toString(),
  })
  if (!response.ok) throw new OcError("Sign-in didn't complete. Try again.", 401)
  return response.json()
}

export async function exchangeCode(cfg: CognitoConfig, code: string, verifier: string, fetchImpl: typeof fetch = fetch): Promise<Tokens> {
  return tokenRequest(cfg, { grant_type: "authorization_code", code, code_verifier: verifier, redirect_uri: cfg.redirectUri }, fetchImpl)
}

export async function refreshTokens(cfg: CognitoConfig, refreshToken: string, fetchImpl: typeof fetch = fetch): Promise<{ access_token: string; expires_in: number }> {
  return tokenRequest(cfg, { grant_type: "refresh_token", refresh_token: refreshToken }, fetchImpl)
}

export async function revokeToken(cfg: CognitoConfig, refreshToken: string, fetchImpl: typeof fetch = fetch) {
  await fetchImpl(`${cfg.domain}/oauth2/revoke`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: cfg.clientId, token: refreshToken }).toString(),
  }).catch(() => undefined)
}

type Claims = { sub: string; email?: string; name?: string; "cognito:groups"?: string[] }

const verifiers = new Map<string, { verify: (token: string) => Promise<unknown> }>()
export async function verifyIdToken(cfg: CognitoConfig, idToken: string): Promise<Claims> {
  const key = `${cfg.userPoolId}/${cfg.clientId}`
  if (!verifiers.has(key)) verifiers.set(key, CognitoJwtVerifier.create({ userPoolId: cfg.userPoolId, tokenUse: "id", clientId: cfg.clientId }))
  try {
    return (await verifiers.get(key)!.verify(idToken)) as Claims
  } catch {
    throw new OcError("Sign-in couldn't be verified. Try again.", 401)
  }
}

/** Owner comes from the Cognito "owner" group; anyone else is a member. */
export function profileFrom(claims: Claims): Profile {
  const email = claims.email ?? ""
  return {
    id: claims.sub,
    email,
    name: claims.name?.trim() || email || "Someone",
    role: claims["cognito:groups"]?.includes("owner") ? "owner" : "member",
  }
}
