// Server-only: the Team page talks to the cloud's Team API (Cognito), through the gateway, as the owner.
import { cognitoConfig, refreshTokens } from "./cognito"
import { readToken } from "./session"
import { accessTokenForSession } from "./store"
import { OcError } from "@/lib/opencode/errors"
import { gateway } from "@/lib/gateway/client"

export async function teamCall(req: Request, method: string, suffix = "", body?: string) {
  const token = await accessTokenForSession(readToken(req), (r) => refreshTokens(cognitoConfig(req), r))
  if (!token) throw new OcError("Sign in again.", 401)
  const res = await gateway(`/internal/cloud/v1/admin/users${suffix}`, { method, body, accessToken: token.access_token })
  const text = await res.text()
  if (res.ok) return new Response(text || null, { status: res.status, headers: { "content-type": "application/json" } })
  // The cloud says { code, message }; the app's client reads { error }.
  let error = `Request failed (${res.status})`
  try {
    error = (JSON.parse(text) as { message?: string }).message || error
  } catch {
    // Not JSON.
  }
  return Response.json({ error }, { status: res.status })
}
