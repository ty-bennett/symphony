"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import { api } from "@/lib/opencode/client"
import {
  initialAgentState,
  mergeSnapshot,
  normalizePermission,
  reduceEvent,
  sortedMessages,
  sortedSessions,
  unwrapEvent,
  type AgentState,
} from "@/lib/opencode/state"
import type {
  FileDiff,
  MessageWithParts,
  OcEvent,
  ProvidersResponse,
  RawPermission,
  Session,
  Todo,
} from "@/lib/opencode/types"
import { Composer } from "./Composer"
import { DiffPanel } from "./DiffPanel"
import { PermissionCard } from "./PermissionCard"
import { SessionList } from "./SessionList"
import { Thread } from "./Thread"
import styles from "./CodingAgent.module.css"

export type CodingAgentProps = {
  /** Base path of the app's opencode proxy routes. */
  apiBase?: string
  initialSessionId?: string | null
  onSessionChange?: (sessionId: string | null) => void
}

type Connection = "connecting" | "live" | "reconnecting"

const MODEL_KEY = "symphony-agent-model"

export function CodingAgent({ apiBase = "/api/oc", initialSessionId = null, onSessionChange }: CodingAgentProps) {
  const [state, setState] = useState<AgentState>(initialAgentState)
  const [activeId, setActiveId] = useState<string | null>(initialSessionId)
  const [connection, setConnection] = useState<Connection>("connecting")
  const [error, setError] = useState<string | null>(null)
  const [providers, setProviders] = useState<ProvidersResponse | null>(null)
  const [model, setModel] = useState("")
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [changesOpen, setChangesOpen] = useState(true)

  // The EventSource handler is created once, so it reads the active session from a ref.
  const activeRef = useRef<string | null>(initialSessionId)
  const onSessionChangeRef = useRef(onSessionChange)
  useEffect(() => {
    onSessionChangeRef.current = onSessionChange
  }, [onSessionChange])

  const report = useCallback((e: unknown) => {
    setError(e instanceof Error ? e.message : String(e))
  }, [])

  // ---------- loading ----------

  const loadSessions = useCallback(async () => {
    const sessions = await api<Session[]>(apiBase, "/sessions")
    setState((s) => ({ ...s, sessions: Object.fromEntries(sessions.map((x) => [x.id, x])) }))
  }, [apiBase])

  const loadPermissions = useCallback(async () => {
    const pending = await api<RawPermission[]>(apiBase, "/permissions")
    setState((s) => ({
      ...s,
      permissions: { ...s.permissions, ...Object.fromEntries(pending.map((p) => [p.id, normalizePermission(p)])) },
    }))
  }, [apiBase])

  const refreshDiff = useCallback(
    async (sessionId: string) => {
      try {
        const [diff, todos] = await Promise.all([
          api<FileDiff[]>(apiBase, `/sessions/${encodeURIComponent(sessionId)}/diff`),
          api<Todo[]>(apiBase, `/sessions/${encodeURIComponent(sessionId)}/todo`),
        ])
        if (activeRef.current !== sessionId) return
        setState((s) => ({ ...s, diff, todos }))
      } catch {
        // The diff is secondary; a failed refresh keeps the last one.
      }
    },
    [apiBase],
  )

  const loadHistory = useCallback(
    async (sessionId: string) => {
      setLoadingHistory(true)
      try {
        const [messages, diff, todos] = await Promise.all([
          api<MessageWithParts[]>(apiBase, `/sessions/${encodeURIComponent(sessionId)}/messages`),
          api<FileDiff[]>(apiBase, `/sessions/${encodeURIComponent(sessionId)}/diff`).catch(() => null),
          api<Todo[]>(apiBase, `/sessions/${encodeURIComponent(sessionId)}/todo`).catch(() => []),
        ])
        if (activeRef.current !== sessionId) return
        // Merge, don't replace: events that arrived while this request was in
        // flight are newer than the snapshot.
        setState((s) => ({
          ...s,
          messages: mergeSnapshot(s.messages, messages),
          diff: s.diff ?? diff,
          todos: s.todos.length ? s.todos : todos,
        }))
      } catch (e) {
        if (activeRef.current !== sessionId) return
        report(e)
      } finally {
        if (activeRef.current === sessionId) setLoadingHistory(false)
      }
    },
    [apiBase, report],
  )

  const selectSession = useCallback((id: string | null) => {
    activeRef.current = id
    setActiveId(id)
    // Reset in the same update as the switch, so no event for the new session is lost.
    setState((s) => ({ ...s, messages: {}, diff: null, todos: [], sessionError: null }))
    setSessionsOpen(false)
    onSessionChangeRef.current?.(id)
  }, [])

  // First load: is opencode up, which models exist, which sessions and approvals are waiting.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await api(apiBase, "/health")
      } catch (e) {
        if (!cancelled) report(e)
        return
      }
      const results = await Promise.allSettled([
        loadSessions(),
        loadPermissions(),
        api<ProvidersResponse>(apiBase, "/providers").then((p) => !cancelled && setProviders(p)),
      ])
      const failed = results.find((r): r is PromiseRejectedResult => r.status === "rejected")
      if (failed && !cancelled) report(failed.reason)
    })()
    return () => {
      cancelled = true
    }
  }, [apiBase, loadSessions, loadPermissions, report])

  useEffect(() => {
    try {
      const saved = localStorage.getItem(MODEL_KEY)
      if (saved) setModel(saved)
    } catch {
      // Storage unavailable; use the server default.
    }
    // Changes sits inline on wide screens and as a drawer below 1100px; follow the breakpoint.
    const narrow = window.matchMedia("(max-width: 1100px)")
    const sync = () => {
      setChangesOpen(!narrow.matches)
      setSessionsOpen(false)
    }
    sync()
    narrow.addEventListener("change", sync)
    return () => narrow.removeEventListener("change", sync)
  }, [])

  useEffect(() => {
    if (activeId) void loadHistory(activeId)
  }, [activeId, loadHistory])

  // ---------- live events ----------

  const resyncRef = useRef<() => void>(() => {})
  useEffect(() => {
    resyncRef.current = () => {
      void loadSessions().catch(() => {})
      void loadPermissions().catch(() => {})
      if (activeRef.current) void loadHistory(activeRef.current)
    }
  }, [loadSessions, loadPermissions, loadHistory])

  const handleEvent = useCallback(
    (event: OcEvent) => {
      const active = activeRef.current
      setState((s) => reduceEvent(s, event, active).state)
      const sid = event.properties.sessionID
      const status = (event.properties.status as { type?: string } | undefined)?.type
      if (sid && sid === active && (event.type === "session.idle" || (event.type === "session.status" && status === "idle"))) {
        void refreshDiff(sid as string)
      }
    },
    [refreshDiff],
  )

  useEffect(() => {
    let source: EventSource | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let attempts = 0
    let disposed = false

    const connect = () => {
      source = new EventSource(`${apiBase}/events`)
      source.onopen = () => {
        if (attempts > 0) resyncRef.current()
        attempts = 0
        setConnection("live")
      }
      source.onmessage = (message) => {
        let data: unknown
        try {
          data = JSON.parse(message.data)
        } catch {
          return
        }
        const event = unwrapEvent(data)
        if (event) handleEvent(event)
      }
      source.onerror = () => {
        setConnection("reconnecting")
        attempts++
        // The browser retries on its own unless the stream failed outright.
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
  }, [apiBase, handleEvent])

  // ---------- actions ----------

  const createSession = useCallback(async () => {
    try {
      const session = await api<Session>(apiBase, "/sessions", { method: "POST", body: JSON.stringify({}) })
      setState((s) => ({ ...s, sessions: { ...s.sessions, [session.id]: session } }))
      selectSession(session.id)
    } catch (e) {
      report(e)
    }
  }, [apiBase, report, selectSession])

  const deleteSession = useCallback(
    async (id: string) => {
      try {
        await api(apiBase, `/sessions/${encodeURIComponent(id)}`, { method: "DELETE" })
        setState((s) => {
          const sessions = { ...s.sessions }
          delete sessions[id]
          return { ...s, sessions }
        })
        if (activeRef.current === id) selectSession(null)
      } catch (e) {
        report(e)
      }
    },
    [apiBase, report, selectSession],
  )

  const send = useCallback(
    async (text: string) => {
      const body = JSON.stringify({ text, ...(model ? { model } : {}) })
      const id = activeRef.current
      try {
        if (id) {
          setState((s) => ({ ...s, status: { ...s.status, [id]: "busy" }, sessionError: null }))
          await api(apiBase, `/sessions/${encodeURIComponent(id)}/prompt`, { method: "POST", body })
        } else {
          const { sessionId } = await api<{ sessionId: string }>(apiBase, "/tasks", { method: "POST", body })
          selectSession(sessionId)
          setState((s) => ({ ...s, status: { ...s.status, [sessionId]: "busy" } }))
        }
        return true
      } catch (e) {
        if (id) setState((s) => ({ ...s, status: { ...s.status, [id]: "idle" } }))
        report(e)
        return false
      }
    },
    [apiBase, model, report, selectSession],
  )

  const stop = useCallback(async () => {
    const id = activeRef.current
    if (!id) return
    try {
      await api(apiBase, `/sessions/${encodeURIComponent(id)}/abort`, { method: "POST" })
    } catch (e) {
      report(e)
    }
  }, [apiBase, report])

  const answerPermission = useCallback(
    async (sessionId: string, permissionId: string, response: "once" | "always" | "reject") => {
      try {
        await api(apiBase, `/sessions/${encodeURIComponent(sessionId)}/permissions/${encodeURIComponent(permissionId)}`, {
          method: "POST",
          body: JSON.stringify({ response }),
        })
        setState((s) => {
          const permissions = { ...s.permissions }
          delete permissions[permissionId]
          return { ...s, permissions }
        })
      } catch (e) {
        report(e)
      }
    },
    [apiBase, report],
  )

  const chooseModel = useCallback((value: string) => {
    setModel(value)
    try {
      if (value) localStorage.setItem(MODEL_KEY, value)
      else localStorage.removeItem(MODEL_KEY)
    } catch {
      // Ignore storage errors.
    }
  }, [])

  // ---------- derived ----------

  const sessions = useMemo(() => sortedSessions(state.sessions), [state.sessions])
  const messages = useMemo(() => sortedMessages(state.messages), [state.messages])
  const active = activeId ? state.sessions[activeId] : undefined
  const busy = activeId ? state.status[activeId] === "busy" || state.status[activeId] === "retry" : false
  const permissions = Object.values(state.permissions)
  const activePermissions = permissions.filter((p) => p.sessionID === activeId)
  const waiting = new Set(permissions.map((p) => p.sessionID))
  const bannerError = error ?? state.sessionError

  return (
    <div className={styles.root}>
      <div className={sessionsOpen ? `${styles.sessions} ${styles.drawerOpen}` : styles.sessions}>
        <SessionList
          sessions={sessions}
          activeId={activeId}
          status={state.status}
          waiting={waiting}
          onSelect={selectSession}
          onCreate={createSession}
          onDelete={deleteSession}
        />
      </div>

      <section className={styles.center} aria-label="Conversation">
        <header className={styles.header}>
          <Button
            variant="icon"
            className={styles.sessionsToggle}
            aria-label="Show sessions"
            aria-expanded={sessionsOpen}
            onClick={() => setSessionsOpen((v) => !v)}
          >
            <Icon name="sidebar" size={18} />
          </Button>
          <div className={styles.headerTitle}>
            <h1>{active?.title || (activeId ? "Session" : "Coding agent")}</h1>
            <p className={styles.connection} aria-live="polite">
              <span className={styles[`dot_${connection}`]} aria-hidden="true" />
              {connection === "live" ? "Live" : connection === "connecting" ? "Connecting" : "Reconnecting"}
            </p>
          </div>
          <Button
            variant="secondary"
            aria-expanded={changesOpen}
            aria-controls="agent-changes"
            onClick={() => setChangesOpen((v) => !v)}
          >
            Changes
            {state.diff?.length ? <span className={styles.count}>{state.diff.length}</span> : null}
          </Button>
        </header>

        {bannerError ? (
          <div className={styles.banner} role="alert">
            <Icon name="alert" size={16} />
            <p>{bannerError}</p>
            <Button
              variant="icon"
              aria-label="Dismiss"
              onClick={() => {
                setError(null)
                setState((s) => ({ ...s, sessionError: null }))
              }}
            >
              <Icon name="close" size={16} />
            </Button>
          </div>
        ) : null}

        <Thread
          sessionId={activeId}
          messages={messages}
          loading={loadingHistory && messages.length === 0}
          busy={busy}
        >
          {activePermissions.map((p) => (
            <PermissionCard key={p.id} permission={p} onAnswer={(r) => answerPermission(p.sessionID, p.id, r)} />
          ))}
        </Thread>

        <Composer
          busy={busy}
          quiet={activePermissions.length > 0}
          providers={providers}
          model={model}
          onModelChange={chooseModel}
          onSend={send}
          onStop={stop}
          placeholder={activeId ? "Ask for a change, a fix or an explanation" : "Describe a coding task to start a session"}
        />
      </section>

      <div id="agent-changes" className={changesOpen ? `${styles.changes} ${styles.drawerOpen}` : styles.changes}>
        <DiffPanel diff={state.diff} todos={state.todos} onClose={() => setChangesOpen(false)} hasSession={Boolean(activeId)} />
      </div>

      {sessionsOpen || changesOpen ? (
        <button
          type="button"
          className={styles.scrim}
          aria-label="Close panel"
          tabIndex={-1}
          onClick={() => {
            setSessionsOpen(false)
            setChangesOpen(false)
          }}
        />
      ) : null}
    </div>
  )
}
