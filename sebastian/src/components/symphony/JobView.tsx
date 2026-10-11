"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { FileEntry } from "@/components/coding-agent/DiffPanel"
import { PermissionCard } from "@/components/coding-agent/PermissionCard"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import { PlaceLabel } from "@/components/ui/PlaceLabel"
import { api, relativeTime } from "@/lib/opencode/client"
import { initialAgentState, mergeSnapshot, normalizePermission, reduceEvent, sortedMessages, type AgentState } from "@/lib/opencode/state"
import type { FileDiff, MessageWithParts, Part, RawPermission } from "@/lib/opencode/types"
import { useDebounced, useEvents } from "@/lib/opencode/useEvents"
import { formatCost, PLACES } from "@/lib/symphony/places"
import { appOfTool, describeAppTool, N8N_SERVER } from "@/lib/symphony/apps"
import type { AppsOverview } from "@/lib/symphony/apps-server"
import { restoreRedactions } from "@/lib/symphony/routing"
import { formatSaved } from "@/lib/symphony/savings"
import type { Job, Rates } from "@/lib/symphony/types"
import styles from "./page.module.css"

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

function textOf(m: MessageWithParts | undefined) {
  return (m?.parts ?? [])
    .filter((p) => p.type === "text" && !p.synthetic && !p.ignored && p.text)
    .map((p) => p.text)
    .join("\n\n")
}

type Names = Record<string, string>

function workflowOf(part: Part | undefined): string | undefined {
  const id = part?.state?.input?.workflowId
  return typeof id === "string" ? id : undefined
}

const isAppTool = (tool?: string) => Boolean(tool && (tool.startsWith(`${N8N_SERVER}_`) || appOfTool(tool)))

/** What an app action was given, e.g. to: jane@example.com */
function appInputs(part: Part | undefined): string[] {
  const own = part?.tool ? appOfTool(part.tool) : null
  const inputs = part?.state?.input?.inputs as { webhookData?: { body?: unknown } } | undefined
  const body = own ? part?.state?.input : (inputs?.webhookData?.body ?? inputs)
  if (!body || typeof body !== "object") return []
  return Object.entries(body as Record<string, unknown>)
    .filter(([key]) => key !== "type")
    .slice(0, 6)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`.slice(0, 400))
}

function stepLabel(part: Part, names: Names = {}): string {
  const id = workflowOf(part)
  const app = part.tool ? describeAppTool(part.tool, id ? names[id] : undefined) : null
  if (app) return app
  const s = part.state
  if (s?.title) return s.title
  const input = s?.input ?? {}
  for (const key of ["description", "command", "filePath", "path", "pattern", "url"]) {
    const v = input[key]
    if (typeof v === "string" && v) return v
  }
  return part.tool ?? "Step"
}

function StepIcon({ status }: { status?: string }) {
  if (status === "completed") return <Icon name="check" size={16} style={{ color: "var(--good)", flex: "none" }} aria-label="Done" />
  if (status === "error") return <Icon name="alert" size={16} style={{ color: "var(--warn)", flex: "none" }} aria-label="Failed" />
  if (status === "running") return <span className={styles.spinner} role="img" aria-label="Running" />
  return <Icon name="clock" size={16} style={{ color: "var(--muted)", flex: "none" }} aria-label="Waiting" />
}

export function JobView({ id }: { id: string }) {
  const [job, setJob] = useState<Job | null>(null)
  const [redactions, setRedactions] = useState<Record<string, string>>({})
  const [rates, setRates] = useState<Rates | null>(null)
  const [names, setNames] = useState<Names>({})
  const namesLoaded = useRef(false)
  const [state, setState] = useState<AgentState>(initialAgentState)
  const [error, setError] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)
  const [acting, setActing] = useState(false)
  const idRef = useRef(id)

  const loadJob = useCallback(async () => {
    try {
      const data = await api<{ job: Job; rates: Rates; redactions: Record<string, string> }>("/api/symphony", `/jobs/${encodeURIComponent(id)}`)
      setJob(data.job)
      setRates(data.rates)
      setRedactions(data.redactions)
    } catch (e) {
      if (message(e).includes("not found")) setMissing(true)
      else setError(message(e))
    }
  }, [id])

  const loadThread = useCallback(async () => {
    try {
      const [messages, diff, pending] = await Promise.all([
        api<MessageWithParts[]>("/api/oc", `/sessions/${encodeURIComponent(id)}/messages`),
        api<FileDiff[]>("/api/oc", `/sessions/${encodeURIComponent(id)}/diff`).catch(() => null),
        api<RawPermission[]>("/api/oc", "/permissions").catch(() => [] as RawPermission[]),
      ])
      setState((s) => ({
        ...s,
        messages: mergeSnapshot(s.messages, messages),
        diff: diff ?? s.diff,
        permissions: Object.fromEntries(pending.filter((p) => p.sessionID === id).map((p) => [p.id, normalizePermission(p)])),
      }))
    } catch (e) {
      if (!message(e).includes("not found")) setError(message(e))
    }
  }, [id])

  useEffect(() => {
    idRef.current = id
    setState(initialAgentState)
    void loadJob()
    void loadThread()
  }, [id, loadJob, loadThread])

  const refreshJob = useDebounced(() => void loadJob(), 300)
  const refreshDiff = useDebounced(() => {
    void api<FileDiff[]>("/api/oc", `/sessions/${encodeURIComponent(id)}/diff`)
      .then((diff) => setState((s) => ({ ...s, diff })))
      .catch(() => {})
  }, 500)

  useEvents(
    "/api/oc",
    (event) => {
      setState((s) => reduceEvent(s, event, idRef.current).state)
      const sid = event.properties.sessionID ?? (event.properties.info as { id?: string } | undefined)?.id
      if (sid !== idRef.current) return
      if (event.type.startsWith("session.") || event.type.startsWith("permission.")) refreshJob()
      if (event.type === "session.idle" || event.type === "session.status") refreshDiff()
    },
    () => {
      void loadJob()
      void loadThread()
    },
  )

  async function act(path: "undo" | "redo") {
    setActing(true)
    try {
      await api("/api/symphony", `/jobs/${encodeURIComponent(id)}/${path}`, { method: "POST" })
      await Promise.all([loadJob(), loadThread()])
    } catch (e) {
      setError(message(e))
    } finally {
      setActing(false)
    }
  }

  async function answer(permissionId: string, response: "once" | "always" | "reject") {
    try {
      await api("/api/oc", `/sessions/${encodeURIComponent(id)}/permissions/${encodeURIComponent(permissionId)}`, {
        method: "POST",
        body: JSON.stringify({ response }),
      })
      setState((s) => {
        const permissions = { ...s.permissions }
        delete permissions[permissionId]
        return { ...s, permissions }
      })
    } catch (e) {
      setError(message(e))
    }
  }

  const messages = useMemo(() => sortedMessages(state.messages), [state.messages])
  const asked = restoreRedactions(textOf(messages.find((m) => m.info.role === "user")), redactions)
  const assistant = messages.filter((m) => m.info.role === "assistant")
  const lastAssistant = assistant[assistant.length - 1]
  const result = restoreRedactions(textOf(lastAssistant), redactions)
  const steps = assistant.flatMap((m) => m.parts).filter((p) => p.type === "tool")
  const failure = lastAssistant?.info.error
  const usesApps = steps.some((p) => isAppTool(p.tool)) || Object.values(state.permissions).some((p) => isAppTool(p.kind))
  useEffect(() => {
    if (!usesApps || namesLoaded.current) return
    namesLoaded.current = true
    api<AppsOverview>("/api/symphony", "/apps")
      .then((o) => {
        const all = [...o.apps.flatMap((a) => [...a.ready, ...a.off]), ...o.other]
        setNames(Object.fromEntries(all.map((w) => [w.id, w.name])))
      })
      .catch(() => {})
  }, [usesApps])
  const permissions = Object.values(state.permissions)
    .filter((p) => p.sessionID === id)
    .map((p) => {
      if (!isAppTool(p.kind)) return p
      const part = steps.find((s) => s.callID && s.callID === p.callID)
      const wf = workflowOf(part)
      return {
        ...p,
        title: describeAppTool(p.kind!, wf ? names[wf] : undefined) ?? p.title,
        details: appInputs(part).map((d) => restoreRedactions(d, redactions)),
      }
    })
  const busy = job?.status === "working" || state.status[id] === "busy"
  const files = state.diff ?? []

  if (missing) {
    return (
      <div className={styles.page}>
        <p className={styles.crumbs}>
          <Link href="/work">Work</Link>
        </p>
        <h1 style={{ fontSize: "var(--fs-title)" }}>This job doesn&apos;t exist anymore</h1>
        <p className={styles.muted}>It may have been deleted.</p>
      </div>
    )
  }

  const status = !job
    ? "Loading"
    : job.status === "needs-ok"
      ? "Waiting for your OK"
      : job.status === "working"
        ? "Working"
        : job.status === "undone"
          ? "Undone"
          : `Finished ${relativeTime(job.updated)}`

  return (
    <div className={styles.page}>
      <header className={styles.headerText}>
        <nav aria-label="Breadcrumb" className={styles.crumbs}>
          <Link href="/work">All work</Link>
        </nav>
        <h1 style={{ fontSize: "var(--fs-title)", lineHeight: 1.15 }}>{job?.title ?? " "}</h1>
        <p className={job?.status === "needs-ok" ? styles.warnText : styles.muted} aria-live="polite">
          {status}
        </p>
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
          {asked ? (
            <div className={styles.quote}>
              <p className={styles.meta}>You asked</p>
              <p style={{ fontSize: "var(--fs-lead)" }}>{asked}</p>
            </div>
          ) : null}

          {permissions.map((p) => (
            <PermissionCard key={p.id} permission={p} onAnswer={(r) => answer(p.id, r)} />
          ))}

          {steps.length || busy ? (
            <section className={styles.section} aria-labelledby="steps-h">
              <h2 id="steps-h">Steps</h2>
              <ol className={styles.steps}>
                {steps.map((p) => (
                  <li key={p.id}>
                    <StepIcon status={p.state?.status} />
                    <span className={styles.stepText} title={stepLabel(p, names)}>
                      {restoreRedactions(stepLabel(p, names), redactions)}
                    </span>
                  </li>
                ))}
                {busy && !permissions.length ? (
                  <li>
                    <span className={styles.spinner} aria-hidden="true" />
                    <span className={styles.muted}>Working</span>
                  </li>
                ) : null}
              </ol>
            </section>
          ) : null}

          {job?.status === "undone" ? (
            <div className={styles.note} style={{ background: "var(--raised)" }}>
              <Icon name="undo" size={16} />
              <p style={{ flex: 1 }}>This job was undone. The files it changed are back the way they were.</p>
              <Button variant="secondary" disabled={acting} onClick={() => void act("redo")}>
                Redo
              </Button>
            </div>
          ) : null}

          {result && !busy ? (
            <section className={styles.section} aria-labelledby="result-h">
              <h2 id="result-h">Result</h2>
              <p className={styles.result}>{result}</p>
            </section>
          ) : null}

          {failure && !busy ? (
            <div className={styles.error}>
              <Icon name="alert" size={16} />
              <p>{failure.name === "MessageAbortedError" ? "This job was stopped." : failure.data?.message || failure.name || "Something went wrong."}</p>
            </div>
          ) : null}

          {job && job.status !== "undone" && !busy && files.length ? (
            <div className={styles.inline}>
              <Button variant="secondary" disabled={acting} onClick={() => void act("undo")}>
                <Icon name="undo" size={16} />
                Undo all
              </Button>
              <span className={styles.meta}>
                Puts back the {files.length} {files.length === 1 ? "file" : "files"} this job changed.
              </span>
            </div>
          ) : null}

          <Link href={`/coding-agent?session=${encodeURIComponent(id)}`} className={styles.meta} style={{ alignSelf: "flex-start" }}>
            Open in the coding agent
          </Link>
        </div>

        <aside className={styles.aside}>
          <section className={styles.section} aria-labelledby="where-h">
            <h2 id="where-h">Where this ran</h2>
            {job ? (
              <>
                <PlaceLabel place={job.place} />
                <dl className={styles.facts}>
                  {job.reason ? (
                    <div>
                      <dt>Why</dt>
                      <dd>{job.reason.charAt(0).toUpperCase() + job.reason.slice(1)}.</dd>
                    </div>
                  ) : null}
                  {job.model ? (
                    <div>
                      <dt>Model</dt>
                      <dd>{job.model}</dd>
                    </div>
                  ) : null}
                  {job.checkFirst !== undefined ? (
                    <div>
                      <dt>Check with me first</dt>
                      <dd>{job.checkFirst ? "On" : "Off"}</dd>
                    </div>
                  ) : null}
                  {Object.keys(redactions).length ? (
                    <div>
                      <dt>Removed before sending</dt>
                      <dd>
                        {Object.keys(redactions).length} {Object.keys(redactions).length === 1 ? "detail" : "details"}, kept{" "}
                        {PLACES.device.short}
                      </dd>
                    </div>
                  ) : null}
                  <div>
                    <dt>Cost</dt>
                    <dd>{formatCost(job.cost)}</dd>
                  </div>
                  {job.place !== "open" && job.saved !== undefined && job.wouldHaveCost ? (
                    <div>
                      <dt>Saved</dt>
                      <dd>
                        <span style={{ color: job.saved > 0 ? "var(--good)" : undefined, fontWeight: 600 }}>{formatSaved(job.saved)}</span>
                        <span className={styles.meta}>
                          {" "}
                          It would have cost {formatCost(job.wouldHaveCost)} at {rates?.label ?? "open-cloud"} prices.{" "}
                          <Link href="/privacy#saved">How this is worked out</Link>
                        </span>
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </>
            ) : (
              <p className={styles.muted}>Loading</p>
            )}
          </section>

          <section className={styles.section} aria-labelledby="changes-h" style={{ borderTop: "1px solid var(--line)", paddingTop: 20 }}>
            <h2 id="changes-h">Changes</h2>
            {job?.status === "undone" ? (
              <p className={styles.muted}>Undone.</p>
            ) : files.length === 0 ? (
              <p className={styles.muted}>{busy ? "Nothing changed yet." : "No files changed."}</p>
            ) : (
              <ul className={styles.list} style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {files.map((f, i) => (
                  <FileEntry key={f.file ?? i} file={f} />
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </div>
  )
}
