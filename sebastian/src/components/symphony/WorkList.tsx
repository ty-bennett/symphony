"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/Button"
import { EmptyState } from "@/components/ui/EmptyState"
import { Icon } from "@/components/ui/Icon"
import { PlaceLabel } from "@/components/ui/PlaceLabel"
import { api } from "@/lib/opencode/client"
import { useDebounced, useEvents } from "@/lib/opencode/useEvents"
import { formatCost } from "@/lib/symphony/places"
import type { Job, JobStatus } from "@/lib/symphony/types"
import { statusText } from "./TodayView"
import styles from "./page.module.css"

const FILTERS: { id: JobStatus | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "needs-ok", label: "Needs your OK" },
  { id: "working", label: "Working" },
  { id: "done", label: "Done" },
  { id: "undone", label: "Undone" },
]

export function WorkList() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [filter, setFilter] = useState<JobStatus | "all">("all")
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setJobs((await api<{ jobs: Job[] }>("/api/symphony", "/jobs")).jobs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  const refresh = useDebounced(() => void load(), 400)
  useEvents("/api/oc", (event) => {
    if (event.type.startsWith("session.") || event.type.startsWith("permission.")) refresh()
  }, refresh)

  const counts = (id: JobStatus | "all") => (jobs ?? []).filter((j) => id === "all" || j.status === id).length
  const shown = (jobs ?? []).filter((j) => filter === "all" || j.status === filter)

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <h1>Work</h1>
          <p className={styles.muted}>Every job Symphony has done or is doing, newest first.</p>
        </div>
        <Link href="/" className={styles.reviewLink}>
          New job
        </Link>
      </header>

      {error ? (
        <div className={styles.error} role="alert">
          <Icon name="alert" size={16} />
          <p style={{ flex: 1 }}>{error}</p>
          <Button variant="secondary" onClick={() => void load()}>
            Try again
          </Button>
        </div>
      ) : null}

      <div className={styles.section}>
        <div className={styles.filters} role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              className={filter === f.id ? `${styles.filter} ${styles.filterOn}` : styles.filter}
              onClick={() => setFilter(f.id)}
            >
              {f.label} <span className={styles.meta}>{counts(f.id)}</span>
            </button>
          ))}
        </div>

        {jobs === null ? (
          <p className={styles.muted}>Loading</p>
        ) : shown.length === 0 ? (
          <EmptyState title={filter === "all" ? "No jobs yet" : "Nothing here"}>
            <p>{filter === "all" ? "Start one from Today." : "No jobs match this filter."}</p>
          </EmptyState>
        ) : (
          <ul className={styles.list}>
            {shown.map((job) => (
              <li key={job.id}>
                <Link href={`/work/${job.id}`} className={styles.rowLink}>
                  <span className={styles.rowText}>
                    <span className={styles.rowTitle}>{job.title}</span>
                    <span className={styles.meta}>
                      <span className={job.status === "needs-ok" ? styles.warnText : undefined}>{statusText(job)}</span>
                      {job.by ? ` · by ${job.by.name}` : null}
                    </span>
                  </span>
                  <span className={styles.rowSide}>
                    <PlaceLabel place={job.place} quiet />
                    <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 64, textAlign: "right" }}>{formatCost(job.cost)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
