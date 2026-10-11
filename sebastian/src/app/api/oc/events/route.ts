import { OcError, ocFetch, withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

const PING_MS = 15_000
const encoder = new TextEncoder()

/** True when the bytes seen so far end on an SSE event boundary (a blank line). */
function endsOnBoundary(tail: string): boolean {
  return tail.endsWith("\n\n") || tail.endsWith("\r\n\r\n")
}

// Proxies opencode's SSE stream (GET /event) straight through. A ": ping"
// comment goes out every 15s so proxies don't drop the connection, written
// only between events, never in the middle of one.
export const GET = withOc(async (req) => {
  const upstream = await ocFetch("/event", {
    headers: { accept: "text/event-stream" },
    signal: req.signal,
    cache: "no-store",
  })
  if (!upstream.body) throw new OcError("opencode returned an empty event stream", 502)

  const reader = upstream.body.getReader()
  const decoder = new TextDecoder()
  let tail = "\n\n"
  let timer: ReturnType<typeof setInterval> | undefined
  let closed = false

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const close = () => {
        if (closed) return
        closed = true
        clearInterval(timer)
        reader.cancel().catch(() => {})
        try {
          controller.close()
        } catch {
          // Already closed by the client.
        }
      }

      let owed = false
      const ping = () => {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(": ping\n\n"))
          owed = false
        } catch {
          close()
        }
      }
      // If a ping falls due mid-event, send it right after that event ends.
      timer = setInterval(() => {
        if (endsOnBoundary(tail)) ping()
        else owed = true
      }, PING_MS)

      req.signal.addEventListener("abort", close, { once: true })

      void (async () => {
        try {
          for (;;) {
            const { done, value } = await reader.read()
            if (done || closed) break
            tail = (tail + decoder.decode(value, { stream: true })).slice(-4)
            controller.enqueue(value)
            if (owed && endsOnBoundary(tail)) ping()
          }
        } catch {
          // Upstream dropped; the browser's EventSource will reconnect.
        } finally {
          close()
        }
      })()
    },
    cancel() {
      closed = true
      clearInterval(timer)
      reader.cancel().catch(() => {})
    },
  })

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  })
})
