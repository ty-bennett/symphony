// Server-only: calls to the Jetson's symphony-gateway (localhost), which holds the
// device certificate and forwards to the cloud. The shared secret is the gateway's
// internal.secret file.
import { readFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"

export const gatewayUrl = () => (process.env.SYMPHONY_GATEWAY_URL ?? "http://127.0.0.1:8088").replace(/\/+$/, "")
const secretFile = () => process.env.SYMPHONY_GATEWAY_SECRET_FILE ?? path.join(os.homedir(), ".symphony", "gateway", "internal.secret")

export function gatewaySecret(): string | null {
  try {
    return readFileSync(secretFile(), "utf8").trim() || null
  } catch {
    return null
  }
}

export async function gateway(pathname: string, init: RequestInit & { accessToken?: string } = {}) {
  const secret = gatewaySecret()
  if (!secret) throw new Error("The Symphony gateway isn't set up on this box (no internal.secret).")
  const { accessToken, ...rest } = init
  return fetch(`${gatewayUrl()}${pathname}`, {
    ...rest,
    headers: {
      "content-type": "application/json",
      "x-symphony-internal": secret,
      ...(accessToken ? { authorization: `Bearer ${accessToken}` } : {}),
      ...(rest.headers ?? {}),
    },
  })
}
