"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import { relativeTime } from "@/lib/opencode/client"
import type { Session, SessionStatus } from "@/lib/opencode/types"
import styles from "./CodingAgent.module.css"

export function SessionList({
  sessions,
  activeId,
  status,
  waiting,
  onSelect,
  onCreate,
  onDelete,
}: {
  sessions: Session[]
  activeId: string | null
  status: Record<string, SessionStatus["type"]>
  waiting: Set<string>
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
}) {
  const [confirming, setConfirming] = useState<string | null>(null)
  // Re-render every minute so "5 min ago" stays true.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(timer)
  }, [])

  return (
    <nav className={styles.sessionNav} aria-label="Sessions">
      <div className={styles.sessionHead}>
        <h2>Sessions</h2>
        <Button variant="secondary" onClick={onCreate}>
          <Icon name="plus" size={16} />
          New
        </Button>
      </div>
      {sessions.length === 0 ? (
        <p className={styles.sessionEmpty}>No sessions yet. Describe a task to start one.</p>
      ) : (
        <ul className={styles.sessionItems}>
          {sessions.map((s) => {
            const isActive = s.id === activeId
            const isBusy = status[s.id] === "busy" || status[s.id] === "retry"
            const needsOk = waiting.has(s.id)
            return (
              <li key={s.id} className={isActive ? `${styles.sessionItem} ${styles.sessionActive}` : styles.sessionItem}>
                <button
                  type="button"
                  className={styles.sessionButton}
                  aria-current={isActive ? "true" : undefined}
                  onClick={() => onSelect(s.id)}
                >
                  <span className={styles.sessionTitle}>{s.title || "Untitled session"}</span>
                  <span className={styles.sessionMeta}>
                    {needsOk ? (
                      <span className={styles.metaWarn}>Needs your OK</span>
                    ) : isBusy ? (
                      <span className={styles.metaBusy}>
                        <span className={styles.spinner} aria-hidden="true" />
                        Working
                      </span>
                    ) : (
                      relativeTime(s.time.updated, now)
                    )}
                  </span>
                </button>
                {confirming === s.id ? (
                  <span className={styles.confirm}>
                    <Button
                      variant="text"
                      onClick={() => {
                        setConfirming(null)
                        onDelete(s.id)
                      }}
                    >
                      Delete
                    </Button>
                    <Button variant="icon" aria-label="Keep session" onClick={() => setConfirming(null)}>
                      <Icon name="close" size={16} />
                    </Button>
                  </span>
                ) : (
                  <Button
                    variant="icon"
                    className={styles.deleteButton}
                    aria-label={`Delete ${s.title || "session"}`}
                    onClick={() => setConfirming(s.id)}
                  >
                    <Icon name="trash" size={16} />
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </nav>
  )
}
