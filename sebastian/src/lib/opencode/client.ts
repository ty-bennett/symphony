// Browser-side helpers for the /api/oc routes.

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

export async function api<T>(apiBase: string, path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: init?.body ? { "content-type": "application/json", ...init.headers } : init?.headers,
      cache: "no-store",
    })
  } catch {
    throw new ApiError("Can't reach the app server.", 0)
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { error?: string }
      if (body.error) message = body.error
    } catch {
      // Not JSON.
    }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function relativeTime(ms: number, now = Date.now()): string {
  const seconds = Math.round((now - ms) / 1000)
  if (seconds < 45) return "just now"
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  if (days < 7) return days === 1 ? "yesterday" : `${days} days ago`
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" })
}
