"use client"

import { useEffect, useRef, type ReactNode } from "react"
import { EmptyState } from "@/components/ui/EmptyState"
import type { MessageWithParts } from "@/lib/opencode/types"
import { PartView } from "./parts"
import styles from "./CodingAgent.module.css"

function messageError(m: MessageWithParts): string | null {
  const e = m.info.error
  if (!e) return null
  if (e.name === "MessageAbortedError") return "Stopped."
  return e.data?.message || e.name || "Something went wrong."
}

export function Thread({
  sessionId,
  messages,
  loading,
  busy,
  children,
}: {
  sessionId: string | null
  messages: MessageWithParts[]
  loading: boolean
  busy: boolean
  children?: ReactNode
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)

  // Stay pinned to the bottom while new output streams in, unless the reader scrolled up.
  useEffect(() => {
    const el = scroller.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  })

  function onScroll() {
    const el = scroller.current
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  let body: ReactNode
  if (!sessionId) {
    body = (
      <EmptyState title="Start a coding session">
        <p>
          Describe what you want changed in the repository. The agent plans, edits and runs commands there, and asks before
          anything you&rsquo;ve set to require approval.
        </p>
      </EmptyState>
    )
  } else if (loading) {
    body = <p className={styles.threadNote}>Loading the conversation</p>
  } else if (messages.length === 0) {
    body = <p className={styles.threadNote}>No messages yet. Send the first one below.</p>
  } else {
    body = messages.map((m) => {
      const error = messageError(m)
      if (m.info.role === "user") {
        const text = m.parts
          .filter((p) => p.type === "text" && !p.synthetic && !p.ignored)
          .map((p) => p.text)
          .join("\n")
        return (
          <article key={m.info.id} className={styles.userMessage} aria-label="You">
            {text || m.parts.map((p) => <PartView key={p.id} part={p} />)}
          </article>
        )
      }
      return (
        <article key={m.info.id} className={styles.agentMessage} aria-label="Agent">
          {m.parts.map((p) => (
            <PartView key={p.id} part={p} />
          ))}
          {error ? <p className={error === "Stopped." ? styles.stopped : styles.inlineError}>{error}</p> : null}
        </article>
      )
    })
  }

  return (
    <div ref={scroller} className={styles.thread} onScroll={onScroll}>
      <div className={styles.threadInner}>
        {body}
        {children}
        {busy ? (
          <p className={styles.working} aria-live="polite">
            <span className={styles.spinner} aria-hidden="true" />
            Working
          </p>
        ) : null}
      </div>
    </div>
  )
}
