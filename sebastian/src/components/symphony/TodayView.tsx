"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useMe } from "@/components/shell/AuthGate"
import { Button } from "@/components/ui/Button"
import { Select, Switch } from "@/components/ui/controls"
import { Icon } from "@/components/ui/Icon"
import { PlaceLabel } from "@/components/ui/PlaceLabel"
import { api, relativeTime } from "@/lib/opencode/client"
import { normalizePermission } from "@/lib/opencode/state"
import type { RawPermission } from "@/lib/opencode/types"
import { useDebounced, useEvents } from "@/lib/opencode/useEvents"
import { PLACE_ORDER, PLACES } from "@/lib/symphony/places"
import { routeTask } from "@/lib/symphony/routing"
import { formatSaved } from "@/lib/symphony/savings"
import { startersFor } from "@/lib/symphony/starters"
import type { Job, Place, RouteDecision, Savings, Settings } from "@/lib/symphony/types"
import { ConsentDialog, type ConsentChoice } from "./ConsentDialog"
import styles from "./page.module.css"

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function TodayView() {
  const router = useRouter()
  const { user } = useMe()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [savings, setSavings] = useState<Savings | null>(null)
  const [pending, setPending] = useState<RawPermission[]>([])
  const [text, setText] = useState("")
  const [checkFirst, setCheckFirst] = useState(true)
  const [override, setOverride] = useState<Place | null>(null)
  const [choosing, setChoosing] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [consent, setConsent] = useState<RouteDecision | null>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  const load = useCallback(async () => {
    try {
      const [list, approvals] = await Promise.all([
        api<{ jobs: Job[]; savings: Savings }>("/api/symphony", "/jobs"),
        api<RawPermission[]>("/api/oc", "/permissions"),
      ])
      setJobs(list.jobs)
      setSavings(list.savings)
      setPending(approvals)
    } catch (e) {
      setError(message(e))
    }
  }, [])

  useEffect(() => {
    api<Settings>("/api/symphony", "/settings")
      .then((s) => {
        setSettings(s)
        setCheckFirst(s.checkFirst)
      })
      .catch((e) => setError(message(e)))
    void load()
  }, [load])

  const refresh = useDebounced(() => void load(), 400)
  useEvents("/api/oc", (event) => {
    if (event.type.startsWith("session.") || event.type.startsWith("permission.")) refresh()
  }, refresh)

  const decision = useMemo(() => (settings && text.trim() ? routeTask(text, settings) : null), [settings, text])
  const place = override ?? decision?.place ?? null

  async function start(choice?: ConsentChoice) {
    // Ask before sending private information to the open cloud. The server
    // enforces the same rule (409) in case settings changed in between.
    if (!choice && decision && settings && place === "open") {
      if (decision.privateHits.length > 0 || settings.places.open.rule === "ask-always") {
        setConsent(override ? { ...decision, place: "open", reason: "you chose to run it there" } : decision)
        return
      }
    }
    choice ??= {}
    setStarting(true)
    setError(null)
    try {
      const response = await fetch("/api/symphony/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, checkFirst, place: choice.place ?? override ?? undefined, consent: choice.consent, redact: choice.redact }),
      })
      const body = (await response.json().catch(() => ({}))) as { jobId?: string; error?: string; needsConsent?: boolean; decision?: RouteDecision }
      if (response.status === 409 && body.needsConsent && body.decision) {
        setConsent(body.decision)
        return
      }
      if (!response.ok || !body.jobId) throw new Error(body.error ?? `Couldn't start the job (${response.status})`)
      setConsent(null)
      router.push(`/work/${body.jobId}`)
    } catch (e) {
      setError(message(e))
      setConsent(null)
    } finally {
      setStarting(false)
    }
  }

  // Members only see the approvals that are theirs to give.
  const needsOk = (jobs ?? []).filter((j) => j.status === "needs-ok" && (user?.role === "owner" || j.by?.id === user?.id))
  const recent = (jobs ?? []).filter((j) => j.status !== "needs-ok").slice(0, 5)
  const approvalTitle = (jobId: string) => {
    const p = pending.find((x) => x.sessionID === jobId)
    return p ? normalizePermission(p).title : "Approval"
  }
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })
  const placeChoices = settings
    ? PLACE_ORDER.filter((p) => p === "device" || settings.places[p].enabled)
    : PLACE_ORDER

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <h1>Today</h1>
          <p className={styles.muted}>
            {settings?.businessName ? `${settings.businessName} · ` : ""}
            {today}
          </p>
        </div>
        {user?.role === "owner" && savings && savings.jobs > 0 && savings.saved > 0 ? (
          <Link href="/privacy#saved" className={styles.savedStat}>
            <span className={styles.meta}>Saved this month</span>
            <span className={styles.savedAmount}>{formatSaved(savings.saved)}</span>
          </Link>
        ) : null}
      </header>

      {error ? (
        <div className={styles.error} role="alert">
          <Icon name="alert" size={16} />
          <p style={{ flex: 1 }}>{error}</p>
          <Button variant="icon" aria-label="Dismiss" onClick={() => setError(null)}>
            <Icon name="close" size={16} />
          </Button>
        </div>
      ) : null}

      <div className={styles.columns}>
        <div className={styles.main}>
          <section className={styles.section} aria-labelledby="new-h">
            <h2 id="new-h">New job</h2>
            <label htmlFor="job-text" className={styles.muted}>
              Describe it the way you&apos;d explain it to a new employee.
            </label>
            <textarea
              id="job-text"
              ref={input}
              className={styles.jobInput}
              rows={3}
              value={text}
              onChange={(e) => {
                setText(e.target.value)
                setOverride(null)
                setChoosing(false)
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) void start()
              }}
            />
            {decision && place ? (
              <div className={styles.routeNote} aria-live="polite">
                <Icon name={PLACES[place].icon} size={16} style={{ color: PLACES[place].color }} />
                <p>
                  Runs{" "}
                  <strong style={{ color: PLACES[place].ink, fontWeight: 600 }}>{PLACES[place].short}</strong>
                  {override ? ", because you chose it. " : `, because ${decision.reason}. `}
                  <button type="button" className={styles.linkButton} aria-expanded={choosing} onClick={() => setChoosing((v) => !v)}>
                    Change
                  </button>
                </p>
              </div>
            ) : (
              <p className={styles.meta}>Symphony picks the most private place that can do the job well, and tells you which.</p>
            )}
            {choosing && decision ? (
              <label className={styles.field} style={{ maxWidth: 320 }}>
                <span className={styles.fieldLabel}>Where to run it</span>
                <Select value={place ?? ""} onChange={(e) => setOverride(e.target.value as Place)}>
                  {placeChoices.map((p) => (
                    <option key={p} value={p}>
                      {PLACES[p].name}
                      {p === decision.place ? " (suggested)" : ""}
                    </option>
                  ))}
                </Select>
              </label>
            ) : null}
            <div className={styles.actions}>
              <Switch checked={checkFirst} onChange={setCheckFirst} label="Check with me first" />
              <Button variant="primary" disabled={!text.trim() || starting || !settings} onClick={() => void start()}>
                {starting ? "Starting" : "Start job"}
              </Button>
            </div>
          </section>

          <section className={styles.section} aria-labelledby="ok-h">
            <h2 id="ok-h">
              Needs your OK<span className={styles.count}>{needsOk.length}</span>
            </h2>
            {needsOk.length === 0 ? (
              <p className={styles.muted}>Nothing is waiting for you.</p>
            ) : (
              <ul className={styles.list}>
                {needsOk.map((job) => (
                  <li key={job.id} className={styles.row}>
                    <div className={styles.rowText}>
                      <span className={styles.rowTitle} style={{ fontSize: "var(--fs-lead)" }}>
                        {job.title}
                      </span>
                      <span className={styles.muted}>
                        {approvalTitle(job.id)}. Waiting since {new Date(job.updated).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}.
                      </span>
                    </div>
                    <Link href={`/work/${job.id}`} className={`${styles.reviewLink}`}>
                      Review
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.section} aria-labelledby="starters-h">
            <h2 id="starters-h">Jobs other {settings ? businessNoun(settings.businessType) : "businesses"} start with</h2>
            <ul className={`${styles.list} ${styles.grid2}`}>
              {(settings ? startersFor(settings.businessType) : []).map((s) => (
                <li key={s.title}>
                  <button
                    type="button"
                    className={styles.starter}
                    onClick={() => {
                      setText(s.text)
                      setOverride(null)
                      input.current?.focus()
                    }}
                  >
                    <span className={styles.rowText}>
                      <span className={styles.rowTitle}>{s.title}</span>
                      <span className={styles.muted}>{s.detail}</span>
                    </span>
                    <Icon name="chevronRight" size={16} style={{ color: "var(--muted)", flex: "none" }} />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <section className={`${styles.aside} ${styles.section}`} aria-labelledby="recent-h">
          <h2 id="recent-h" style={{ fontSize: "var(--fs-section)" }}>
            Recent work
          </h2>
          {jobs === null ? (
            <p className={styles.muted}>Loading</p>
          ) : recent.length === 0 ? (
            <p className={styles.muted}>Jobs you start show up here.</p>
          ) : (
            <ul className={styles.list}>
              {recent.map((job) => (
                <li key={job.id}>
                  <Link href={`/work/${job.id}`} className={styles.rowLink} style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
                    <span className={styles.rowText} style={{ flexBasis: "auto" }}>
                      <span className={styles.rowTitle}>{job.title}</span>
                      <span className={styles.meta}>{statusText(job)}</span>
                    </span>
                    <PlaceLabel place={job.place} iconOnly />
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <Link href="/work" style={{ alignSelf: "flex-start", minHeight: 44, display: "inline-flex", alignItems: "center" }}>
            All work
          </Link>
        </section>
      </div>

      {settings ? (
        <ConsentDialog
          key={consent ? text : "closed"}
          decision={consent}
          text={text}
          settings={settings}
          busy={starting}
          onCancel={() => setConsent(null)}
          onChoose={(choice) => void start(choice)}
        />
      ) : null}
    </div>
  )
}

function businessNoun(type: string) {
  if (/bak|caf/i.test(type)) return "bakeries"
  return "businesses"
}

export function statusText(job: Job): string {
  switch (job.status) {
    case "needs-ok":
      return "Needs your OK"
    case "working":
      return "Working"
    case "undone":
      return "Undone"
    default:
      return `Done ${relativeTime(job.updated)}`
  }
}
