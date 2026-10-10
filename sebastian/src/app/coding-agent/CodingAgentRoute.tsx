"use client"

import { useCallback } from "react"
import { CodingAgent } from "@/components/coding-agent/CodingAgent"

// Keeps ?session=<id> in the address bar so sessions can be deep-linked.
export function CodingAgentRoute({ initialSessionId }: { initialSessionId: string | null }) {
  const onSessionChange = useCallback((id: string | null) => {
    const url = new URL(window.location.href)
    if (id) url.searchParams.set("session", id)
    else url.searchParams.delete("session")
    window.history.replaceState(window.history.state, "", url)
  }, [])

  return <CodingAgent apiBase="/api/oc" initialSessionId={initialSessionId} onSessionChange={onSessionChange} />
}
