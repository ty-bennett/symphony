"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Switch } from "@/components/ui/controls"
import { Icon } from "@/components/ui/Icon"
import { api } from "@/lib/opencode/client"
import type { AppsOverview } from "@/lib/symphony/apps-server"
import { PLACES } from "@/lib/symphony/places"
import type { Settings } from "@/lib/symphony/types"
import styles from "./page.module.css"

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))
const pretty = (tool: string) => {
  const words = tool.replace(/_/g, " ")
  return words.charAt(0).toUpperCase() + words.slice(1)
}

type App = AppsOverview["apps"][number]

export function AppsView() {
  const [overview, setOverview] = useState<AppsOverview | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<"" | "Saving" | "Saved">("")
  const [url, setUrl] = useState("")
  const [token, setToken] = useState("")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (fresh = false) => {
    try {
      const data = await api<AppsOverview>("/api/symphony", `/apps${fresh ? "?fresh=1" : ""}`)
      setOverview(data)
      if (data.connection.url) setUrl((u) => u || data.connection.url!)
    } catch (e) {
      setError(message(e))
    }
  }, [])

  useEffect(() => {
    void load()
    api<Settings>("/api/symphony", "/settings")
      .then(setSettings)
      .catch((e) => setError(message(e)))
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [load])

  function updateApps(change: Partial<Settings["apps"]>) {
    if (!settings) return
    const next = { ...settings, apps: { ...settings.apps, ...change } }
    setSettings(next)
    setSaved("Saving")
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try {
        await api("/api/symphony", "/settings", { method: "PUT", body: JSON.stringify(next) })
        setSaved("Saved")
      } catch (e) {
        setSaved("")
        setError(`Couldn't save your changes. ${message(e)}`)
      }
    }, 300)
  }

  async function connect() {
    setBusy(true)
    setError(null)
    try {
      setOverview(await api<AppsOverview>("/api/symphony", "/apps/connection", { method: "PUT", body: JSON.stringify({ url, token }) }))
      setToken("")
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    setBusy(true)
    setError(null)
    try {
      setOverview(await api<AppsOverview>("/api/symphony", "/apps/connection", { method: "DELETE" }))
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  const connection = overview?.connection
  const n8n = connection?.url
  const askFirst = settings?.apps.askFirst ?? true

  return (
    <div className={`${styles.page} ${styles.narrow}`}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <h1>Connected apps</h1>
          <p className={styles.muted}>
            Give Symphony your email, mailing list, books and register. It can do anything you could do in them, and asks before
            it changes or sends anything.
          </p>
        </div>
        <p className={styles.saved} role="status" aria-live="polite">
          {saved}
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

      <section className={styles.section} aria-labelledby="apps-h">
        <div className={styles.actions}>
          <h2 id="apps-h" style={{ fontSize: "var(--fs-section)", lineHeight: 1.25 }}>
            Apps
          </h2>
          <Button variant="secondary" onClick={() => void load(true)}>
            Refresh
          </Button>
        </div>
        <p className={styles.muted}>
          Each app is one workflow in n8n that holds the app&apos;s login and all of its tools. Import it, sign in once, and paste its
          address here.
        </p>
        <ul className={styles.list}>
          {(overview?.apps ?? []).map((app) => (
            <AppRow key={app.id} app={app} askFirst={askFirst} n8n={n8n} onOverview={setOverview} onError={setError} />
          ))}
        </ul>
      </section>

      {settings ? (
        <section className={styles.section} aria-labelledby="rules-h">
          <h2 id="rules-h">What jobs may do</h2>
          <ul className={styles.list}>
            <li className={styles.settingRow}>
              <div className={styles.settingText}>
                <h3>Let jobs use connected apps</h3>
                <p className={styles.muted}>When it&apos;s off, the agent can&apos;t see any of them.</p>
              </div>
              <Switch checked={settings.apps.enabled} onChange={(on) => updateApps({ enabled: on })} label="Let jobs use connected apps" hideLabel />
            </li>
            <li className={styles.settingRow}>
              <div className={styles.settingText}>
                <h3>Ask me before an app changes anything</h3>
                <p className={styles.muted}>
                  Sending, creating, changing or deleting waits for your OK, with exactly what it will do. Searching and reading never
                  ask.
                </p>
              </div>
              <Switch
                checked={settings.apps.askFirst}
                onChange={(on) => updateApps({ askFirst: on })}
                label="Ask me before an app changes anything"
                hideLabel
              />
            </li>
          </ul>
          <p className={styles.meta}>
            Jobs that use an app with private information (email, customers, money) stay {PLACES.device.short} or {PLACES.private.short}{" "}
            unless you say otherwise.
          </p>
        </section>
      ) : null}

      <section className={styles.section} aria-labelledby="n8n-h">
        <h2 id="n8n-h">More n8n workflows</h2>
        <p className={styles.muted}>
          Optional. Connect n8n itself and the agent can also run any workflow you mark &ldquo;Available in MCP&rdquo;, like a custom
          report.
        </p>
        {!connection ? (
          <p className={styles.muted}>Loading</p>
        ) : connection.state === "connected" ? (
          <div className={styles.settingRow} style={{ borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}>
            <Icon name="check" size={20} style={{ color: "var(--good)" }} />
            <div className={styles.settingText}>
              <h3>Connected to n8n</h3>
              <p className={styles.muted}>
                {n8n}
                {connection.agent && connection.agent !== "connected" ? ` · The agent can't use it yet (${connection.agent})` : ""}
              </p>
            </div>
            <div className={styles.settingControls}>
              <a className={styles.reviewLink} href={n8n} target="_blank" rel="noreferrer">
                Open n8n
              </a>
              {connection.source === "saved" ? (
                <Button variant="text" disabled={busy} onClick={() => void disconnect()}>
                  Disconnect
                </Button>
              ) : null}
            </div>
          </div>
        ) : (
          <>
            {connection.state === "error" ? <p className={styles.warnText}>{connection.error}</p> : null}
            {connection.source === "env" ? (
              <p className={styles.meta}>The address and token come from N8N_URL and N8N_MCP_TOKEN on the server.</p>
            ) : (
              <form
                className={styles.connectForm}
                onSubmit={(e) => {
                  e.preventDefault()
                  void connect()
                }}
              >
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>n8n address</span>
                  <input
                    className={styles.textInput}
                    value={url}
                    placeholder="http://localhost:5678"
                    autoComplete="off"
                    inputMode="url"
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>Access token</span>
                  <input className={styles.textInput} type="password" value={token} autoComplete="off" onChange={(e) => setToken(e.target.value)} />
                </label>
                <Button type="submit" variant="secondary" disabled={busy || !url.trim() || !token.trim()}>
                  {busy ? "Connecting" : "Connect"}
                </Button>
              </form>
            )}
            <p className={styles.meta}>
              The token is in n8n under Settings › Instance-level MCP › Connection details. n8n&apos;s tools for building, publishing or
              deleting workflows are never shown to the agent.
            </p>
          </>
        )}
        {overview?.other.length ? (
          <ul className={styles.list} style={{ marginTop: 8 }}>
            {overview.other.map((w) => (
              <li key={w.id} className={styles.row}>
                <span className={styles.rowText}>
                  <span className={styles.rowTitle}>{w.name}</span>
                  {w.description ? <span className={styles.muted}>{w.description}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  )
}

function AppRow({
  app,
  askFirst,
  n8n,
  onOverview,
  onError,
}: {
  app: App
  askFirst: boolean
  n8n?: string
  onOverview: (o: AppsOverview) => void
  onError: (e: string | null) => void
}) {
  const [url, setUrl] = useState(app.server?.url ?? "")
  const [token, setToken] = useState("")
  const [busy, setBusy] = useState(false)
  const server = app.server
  const connected = server?.state === "connected"
  // Open when connected or failing; stays open after connecting or disconnecting.
  const [open, setOpen] = useState(connected || server?.state === "error")
  const tools = server?.tools ?? []

  async function connect() {
    setBusy(true)
    onError(null)
    try {
      onOverview(
        await api<AppsOverview>("/api/symphony", `/apps/${encodeURIComponent(app.id)}`, { method: "PUT", body: JSON.stringify({ url, token }) }),
      )
      setToken("")
    } catch (e) {
      onError(`${app.name}: ${message(e)}`)
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    setBusy(true)
    try {
      onOverview(await api<AppsOverview>("/api/symphony", `/apps/${encodeURIComponent(app.id)}`, { method: "DELETE" }))
      setOpen(true)
    } catch (e) {
      onError(`${app.name}: ${message(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const status = connected
    ? `Connected · ${tools.length} ${tools.length === 1 ? "tool" : "tools"}`
    : server?.state === "error"
      ? "Can't reach it"
      : app.ready.length
        ? `${app.ready.length} ${app.ready.length === 1 ? "action" : "actions"} ready`
        : "Not connected"
  const good = connected || (!server && app.ready.length > 0)

  return (
    <li>
      <details className={styles.app} open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary className={styles.appSummary}>
          <span className={styles.appMark} aria-hidden="true">
            {app.name.charAt(0)}
          </span>
          <span className={styles.rowText}>
            <span className={styles.rowTitle}>{app.name}</span>
            <span className={styles.muted}>{app.about}</span>
          </span>
          <span className={good ? styles.appReady : server?.state === "error" ? styles.warnText : styles.meta}>{status}</span>
          <Icon name="chevronDown" size={16} className={styles.appChevron} />
        </summary>

        <div className={styles.appBody}>
          {connected ? (
            <>
              <ul className={styles.actions2} aria-label={`${app.name} tools`}>
                {tools.map((t) => (
                  <li key={t.name}>
                    <span className={t.reads || !askFirst ? styles.toolRuns : styles.toolAsks}>
                      {t.reads || !askFirst ? "Runs" : "Asks first"}
                    </span>
                    <span>
                      <span className={styles.rowTitle}>{pretty(t.name)}</span>
                      {t.description ? <span className={`${styles.muted} ${styles.block}`}>{t.description}</span> : null}
                    </span>
                  </li>
                ))}
              </ul>
              <div className={styles.inline}>
                <span className={styles.meta} style={{ overflowWrap: "anywhere" }}>
                  {server?.url}
                  {server?.agent && server.agent !== "connected" ? ` · The agent can't use it yet (${server.agent})` : ""}
                </span>
                <Button variant="text" disabled={busy} onClick={() => void disconnect()}>
                  Disconnect
                </Button>
              </div>
            </>
          ) : (
            <>
              {server?.state === "error" ? <p className={styles.warnText}>{server.error}</p> : null}
              <p className={styles.muted}>Then ask things like {app.examples.map((e) => `“${e}”`).join(" or ")}.</p>
              <ol className={styles.howto}>
                {app.workflow ? (
                  <li>
                    <a href={app.workflow} download>
                      Download “{app.name} for Symphony”
                    </a>{" "}
                    . In n8n, create a new workflow, then use the ⋯ menu at the top right › Import from File (or open the file, copy
                    everything and paste it onto the empty canvas). Save it.
                  </li>
                ) : (
                  <li>
                    In n8n, make a workflow with an <strong>MCP Server Trigger</strong> and attach n8n&apos;s {app.name} Tool nodes for what
                    the agent should be able to do.
                  </li>
                )}
                {app.via === "http" ? (
                  <li>
                    Create a <strong>Header Auth</strong> credential named <code>Authorization</code> with the value{" "}
                    <code>Bearer</code> plus your {app.name} access token, and choose it in each tool.
                  </li>
                ) : (
                  <li>
                    Open one of the {app.name} tools, sign in to {app.name}, and choose that login in the other tools
                    {app.id === "mailchimp" ? ". Pick your audience in the subscriber tools" : ""}.
                  </li>
                )}
                <li>
                  On <strong>Symphony connects here</strong>, choose Bearer Auth and make a token: any long password. Use the same one for
                  every app.
                </li>
                <li>
                  <strong>Publish</strong> the workflow (older n8n: switch it to <strong>Active</strong>), then paste the trigger&apos;s{" "}
                  <strong>Production URL</strong> and the token below.
                </li>
              </ol>
              <form
                className={styles.connectForm}
                onSubmit={(e) => {
                  e.preventDefault()
                  void connect()
                }}
              >
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>Production URL</span>
                  <input
                    className={styles.textInput}
                    value={url}
                    placeholder={`${n8n || "http://localhost:5678"}/mcp/symphony-${app.id}`}
                    autoComplete="off"
                    inputMode="url"
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>Token</span>
                  <input className={styles.textInput} type="password" value={token} autoComplete="off" onChange={(e) => setToken(e.target.value)} />
                </label>
                <Button type="submit" variant="primary" disabled={busy || !url.trim()}>
                  {busy ? "Connecting" : `Connect ${app.name}`}
                </Button>
              </form>
            </>
          )}

          {app.ready.length ? (
            <div className={styles.section}>
              <p className={styles.meta}>Also from your n8n workflows:</p>
              <ul className={styles.actions2}>
                {app.ready.map((w) => (
                  <li key={w.id}>
                    <Icon name="check" size={16} style={{ color: "var(--good)", flex: "none", marginTop: 4 }} />
                    <span>
                      <span className={styles.rowTitle}>{w.name}</span>
                      {w.description ? <span className={`${styles.muted} ${styles.block}`}>{w.description}</span> : null}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {app.categories.length ? (
            <p className={styles.meta}>
              <Icon name="lock" size={14} style={{ verticalAlign: -2 }} /> Brings {app.categories.join(" and ").toLowerCase()} into a job, so
              jobs that use it are treated as private.
            </p>
          ) : null}
        </div>
      </details>
    </li>
  )
}
