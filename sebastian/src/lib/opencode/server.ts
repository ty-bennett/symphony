// Server-only helpers for talking to `opencode serve`. Never import this from a
// client component: it reads secrets from the environment.
import { createOpencodeClient, type OpencodeClient } from "@opencode-ai/sdk/v2"

const DEFAULT_URL = "http://127.0.0.1:4096"

export function opencodeUrl(): string {
  return (process.env.OPENCODE_URL || DEFAULT_URL).replace(/\/+$/, "")
}

function authHeader(): string | undefined {
  const password = process.env.OPENCODE_SERVER_PASSWORD
  if (!password) return undefined
  const username = process.env.OPENCODE_SERVER_USERNAME || "opencode"
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`
}

/** fetch that adds opencode's basic-auth header when a password is configured. */
export const authedFetch: typeof fetch = (input, init) => {
  const auth = authHeader()
  if (!auth) return fetch(input, init)
  if (input instanceof Request) {
    const request = new Request(input, init)
    request.headers.set("authorization", auth)
    return fetch(request)
  }
  const headers = new Headers(init?.headers)
  headers.set("authorization", auth)
  return fetch(input, { ...init, headers })
}

// One client per process. Stashed on globalThis so Next's dev reloads reuse it.
const globalForOc = globalThis as unknown as { __symphonyOc?: { url: string; client: OpencodeClient } }

export function oc(): OpencodeClient {
  const url = opencodeUrl()
  const cached = globalForOc.__symphonyOc
  if (cached && cached.url === url) return cached.client
  const client = createOpencodeClient({ baseUrl: url, fetch: authedFetch })
  globalForOc.__symphonyOc = { url, client }
  return client
}

export class OcError extends Error {
  constructor(
    message: string,
    readonly status = 502,
  ) {
    super(message)
  }
}

function describe(error: unknown): string | undefined {
  if (!error) return undefined
  if (typeof error === "string") return error
  if (typeof error === "object") {
    const e = error as { data?: { message?: unknown }; message?: unknown; name?: unknown }
    if (typeof e.data?.message === "string") return e.data.message
    if (typeof e.message === "string") return e.message
    if (typeof e.name === "string") return e.name
  }
  return undefined
}

/** SDK calls resolve to `{ data, error }`. Return data, or throw an OcError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response?: Response }): T {
  if (result.error !== undefined && result.error !== null) {
    const status = result.response?.status
    const message = describe(result.error) ?? "opencode returned an error"
    if (!status && /fetch failed|ECONNREFUSED|ENOTFOUND/i.test(message)) throw unreachable()
    throw new OcError(message, status && status >= 400 ? status : 502)
  }
  return result.data as T
}

function unreachable() {
  return new OcError(`Can't reach opencode at ${opencodeUrl()}. Is \`opencode serve\` running?`, 502)
}

/**
 * Raw call to the opencode server, for endpoints whose SDK method names have
 * changed between versions (prompt_async, diff, todo, permissions).
 */
export async function ocFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json")
  let response: Response
  try {
    response = await authedFetch(`${opencodeUrl()}${path}`, { ...init, headers })
  } catch (error) {
    if (init.signal?.aborted) throw error
    throw unreachable()
  }
  if (!response.ok) {
    let message = `opencode returned ${response.status}`
    try {
      const body = await response.clone().json()
      message = describe(body) ?? message
    } catch {
      // Non-JSON error body; keep the status message.
    }
    throw new OcError(message, response.status >= 400 && response.status < 500 ? response.status : 502)
  }
  return response
}

export async function ocJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await ocFetch(path, init)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/**
 * AUTH HOOK. The coding agent can edit files and run shell commands, so every
 * /api/oc/* route must sit behind the app's login.
 *
 * Wire this to the app's session check when login exists, e.g.
 *   const user = await getCurrentUser(req); if (!user) return deny()
 *
 * Until then: requests are allowed in development and refused in production
 * unless OC_ALLOW_UNAUTHENTICATED=1 is set for a trusted local demo.
 */
export async function checkAuth(req: Request): Promise<Response | null> {
  void req
  if (process.env.NODE_ENV !== "production") return null
  if (process.env.OC_ALLOW_UNAUTHENTICATED === "1") return null
  return Response.json(
    { error: "The coding agent needs a signed-in user. Wire checkAuth() in src/lib/opencode/server.ts to the app's login." },
    { status: 401 },
  )
}

/** Shared wrapper for every /api/oc route: auth first, then JSON errors. */
export function withOc<Ctx>(handler: (req: Request, ctx: Ctx) => Promise<Response>) {
  return async (req: Request, ctx: Ctx): Promise<Response> => {
    const denied = await checkAuth(req)
    if (denied) return denied
    try {
      return await handler(req, ctx)
    } catch (error) {
      if (error instanceof OcError) return Response.json({ error: error.message }, { status: error.status })
      if (error instanceof SyntaxError) return Response.json({ error: "Request body must be JSON" }, { status: 400 })
      const message = error instanceof Error ? error.message : "Unexpected error"
      if (/fetch failed|ECONNREFUSED|ENOTFOUND/i.test(message)) {
        return Response.json({ error: unreachable().message }, { status: 502 })
      }
      return Response.json({ error: message }, { status: 500 })
    }
  }
}

/** Accepts "provider/model" (model ids may contain more slashes). */
export function parseModel(value: unknown): { providerID: string; modelID: string } | undefined {
  if (typeof value !== "string" || !value) return undefined
  const slash = value.indexOf("/")
  if (slash <= 0 || slash === value.length - 1) throw new OcError('model must look like "provider/model"', 400)
  return { providerID: value.slice(0, slash), modelID: value.slice(slash + 1) }
}

export type PromptInput = { text: string; model?: string; agent?: string }

export function readPromptInput(body: unknown): PromptInput {
  const b = (body ?? {}) as Record<string, unknown>
  const text = typeof b.text === "string" ? b.text : typeof b.prompt === "string" ? b.prompt : ""
  if (!text.trim()) throw new OcError("text is required", 400)
  return {
    text,
    model: typeof b.model === "string" && b.model ? b.model : undefined,
    agent: typeof b.agent === "string" && b.agent ? b.agent : undefined,
  }
}

/** Fire-and-forget prompt. opencode answers 204 straight away; progress arrives as events. */
export async function sendPrompt(sessionID: string, input: PromptInput): Promise<void> {
  const model = parseModel(input.model)
  await ocFetch(`/session/${encodeURIComponent(sessionID)}/prompt_async`, {
    method: "POST",
    body: JSON.stringify({
      parts: [{ type: "text", text: input.text }],
      ...(model ? { model } : {}),
      ...(input.agent ? { agent: input.agent } : {}),
    }),
  })
}
