"use client"

import { useEffect, useRef, useState } from "react"
import { unwrapEvent } from "./state"
import type { OcEvent } from "./types"

export type Connection = "connecting" | "live" | "reconnecting"

/**
 * One EventSource on /api/oc/events with reconnect. `onEvent` always sees the
 * latest render's closure; `onReconnect` runs after a dropped stream comes back
 * so the page can refetch anything it missed.
 */
export function useEvents(apiBase: string, onEvent: (event: OcEvent) => void, onReconnect?: () => void): Connection {
  const [connection, setConnection] = useState<Connection>("connecting")
  const handlers = useRef({ onEvent, onReconnect })
  useEffect(() => {
    handlers.current = { onEvent, onReconnect }
  })

  useEffect(() => {
    let source: EventSource | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let attempts = 0
    let disposed = false

    const connect = () => {
      source = new EventSource(`${apiBase}/events`)
      source.onopen = () => {
        if (attempts > 0) handlers.current.onReconnect?.()
        attempts = 0
        setConnection("live")
      }
      source.onmessage = (message) => {
        try {
          const event = unwrapEvent(JSON.parse(message.data))
          if (event) handlers.current.onEvent(event)
        } catch {
          // Ignore malformed events.
        }
      }
      source.onerror = () => {
        setConnection("reconnecting")
        attempts++
        if (source?.readyState === EventSource.CLOSED && !disposed) {
          source.close()
          retry = setTimeout(connect, Math.min(15_000, 1000 * 2 ** Math.min(attempts, 4)))
        }
      }
    }

    connect()
    return () => {
      disposed = true
      clearTimeout(retry)
      source?.close()
    }
  }, [apiBase])

  return connection
}

/** Calls `fn` at most once per `ms`, trailing. */
export function useDebounced(fn: () => void, ms: number) {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const latest = useRef(fn)
  useEffect(() => {
    latest.current = fn
  })
  useEffect(() => () => clearTimeout(timer.current), [])
  return useRef(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => latest.current(), ms)
  }).current
}
