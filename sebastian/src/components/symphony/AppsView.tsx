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
  const readyCount = overview?.apps.filter((a) => a.ready.length).length ?? 0

  return (
    <div className={`${styles.page} ${styles.narrow}`}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <h1>Connected apps</h1>
          <p className={styles.muted}>Let Symphony use the apps you already run the business on. Powered by n8n, running on your Symphony.</p>
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

      <section className={styles.section} aria-labelledby="n8n-h">
        <h2 id="n8n-h">n8n</h2>
        {!connection ? (
          <p className={styles.muted}>Loading</p>
        ) : connection.state === "connected" ? (
          <div className={styles.settingRow} style={{ borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}>
            <Icon name="check" size={20} style={{ color: "var(--good)" }} />
            <div className={styles.settingText}>
              <h3>Connected to n8n</h3>
              <p className={styles.muted}>
                {n8n} · {readyCount ? `${readyCount} ${readyCount === 1 ? "app has" : "apps have"} actions ready` : "no actions ready yet"}
                {connection.agent && connection.agent !== "connected" ? ` · The agent can't use it yet (${connection.agent})` : ""}
              </p>
            </div>
            <div className={styles.settingControls}>
              <a className={styles.reviewLink} href={n8n} target="_blank" rel="noreferrer">
                Open n8n
              </a>
              <Button variant="secondary" disabled={busy} onClick={() => void load(true)}>
                Refresh
              </Button>
              {connection.source === "saved" ? (
                <Button variant="text" disabled={busy} onClick={() => void disconnect()}>
                  Disconnect
                </Button>
              ) : null}
            </div>
          </div>
        ) : (
          <>
            {connection.state === "error" ? (
              <p className={styles.warnText}>{connection.error}</p>
            ) : (
              <p className={styles.muted}>
                n8n holds your app logins and runs the actions. Symphony connects to n8n&apos;s MCP server, so the agent can find and run
                the actions you allow.
              </p>
            )}
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
                  <input
                    className={styles.textInput}
                    type="password"
                    value={token}
                    autoComplete="off"
                    onChange={(e) => setToken(e.target.value)}
                  />
                </label>
                <Button type="submit" variant="primary" disabled={busy || !url.trim() || !token.trim()}>
                  {busy ? "Connecting" : "Connect"}
                </Button>
              </form>
            )}
            <ol className={styles.howto}>
              <li>
                In n8n, open <strong>Settings › Instance-level MCP</strong> and turn on <strong>Enable MCP access</strong>.
              </li>
              <li>
                Choose <strong>Connect a client</strong>, open the <strong>API key</strong> tab, and copy the token.
              </li>
              <li>Paste n8n&apos;s address and the token here.</li>
            </ol>
          </>
        )}
      </section>

      {settings ? (
        <section className={styles.section} aria-labelledby="rules-h">
          <h2 id="rules-h">What jobs may do</h2>
          <ul className={styles.list}>
            <li className={styles.settingRow}>
              <div className={styles.settingText}>
                <h3>Let jobs use connected apps</h3>
                <p className={styles.muted}>When it&apos;s off, the agent can&apos;t see n8n at all.</p>
              </div>
              <Switch checked={settings.apps.enabled} onChange={(on) => updateApps({ enabled: on })} label="Let jobs use connected apps" hideLabel />
            </li>
            <li className={styles.settingRow}>
              <div className={styles.settingText}>
                <h3>Ask me before an app action runs</h3>
                <p className={styles.muted}>
                  Actions can send emails or change things in your apps. Finding actions and reading their results never asks.
                </p>
              </div>
              <Switch
                checked={settings.apps.askFirst}
                onChange={(on) => updateApps({ askFirst: on })}
                label="Ask me before an app action runs"
                hideLabel
              />
            </li>
          </ul>
          <p className={styles.meta}>
            Jobs that mention a connected app count as private, so they stay {PLACES.device.short} or {PLACES.private.short} unless you
            say otherwise. n8n&apos;s tools for building, publishing or deleting workflows are never shown to the agent.
          </p>
        </section>
      ) : null}

      <section className={styles.section} aria-labelledby="apps-h">
        <h2 id="apps-h">Apps</h2>
        <ul className={styles.list}>
          {(overview?.apps ?? []).map((app) => (
            <AppRow key={app.id} app={app} n8n={connection?.state === "connected" ? n8n : undefined} />
          ))}
        </ul>
      </section>

      {overview?.other.length ? (
        <section className={styles.section} aria-labelledby="other-h">
          <h2 id="other-h">Other actions</h2>
          <p className={styles.muted}>Workflows in n8n that the agent can run, not tied to an app above.</p>
          <ul className={styles.list}>
            {overview.other.map((w) => (
              <li key={w.id} className={styles.row}>
                <span className={styles.rowText}>
                  <span className={styles.rowTitle}>{w.name}</span>
                  {w.description ? <span className={styles.muted}>{w.description}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}

function AppRow({ app, n8n }: { app: App; n8n?: string }) {
  const ready = app.ready.length > 0
  return (
    <li>
      <details className={styles.app} open={ready || undefined}>
        <summary className={styles.appSummary}>
          <span className={styles.appMark} aria-hidden="true">
            {app.name.charAt(0)}
          </span>
          <span className={styles.rowText}>
            <span className={styles.rowTitle}>{app.name}</span>
            <span className={styles.muted}>{app.about}</span>
          </span>
          <span className={ready ? styles.appReady : styles.meta}>
            {ready ? `${app.ready.length} ${app.ready.length === 1 ? "action" : "actions"} ready` : "Not set up"}
          </span>
          <Icon name="chevronDown" size={16} className={styles.appChevron} />
        </summary>

        <div className={styles.appBody}>
          {ready ? (
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
          ) : (
            <p className={styles.muted}>Try asking: {app.examples.map((e) => `“${e}”`).join(" or ")}</p>
          )}

          {app.off.length ? (
            <p className={styles.meta}>
              In n8n but not available to Symphony: {app.off.map((w) => w.name).join(", ")}. Publish them and turn on{" "}
              <strong>Available in MCP</strong> in each workflow&apos;s settings.
            </p>
          ) : null}

          {app.categories.length ? (
            <p className={styles.meta}>
              <Icon name="lock" size={14} style={{ verticalAlign: -2 }} /> Brings {app.categories.join(" and ").toLowerCase()} into a job, so
              jobs that use it are treated as private.
            </p>
          ) : null}

          <div className={styles.inline}>
            {app.via === "http" ? (
              <p className={styles.meta} style={{ flexBasis: "100%" }}>
                n8n reaches {app.name} with its HTTP Request node and a {app.name} access token saved as a Header Auth credential
                (Authorization: Bearer …). Import a starter action, pick that credential, then publish it.
              </p>
            ) : (
              <p className={styles.meta} style={{ flexBasis: "100%" }}>
                n8n has a built-in {app.name} node. Make a workflow with a Webhook trigger, sign in to {app.name} in that node, publish it,
                and turn on Available in MCP.
              </p>
            )}
            {app.starters?.map((s) => (
              <a key={s.file} className={styles.reviewLink} href={s.file} download>
                <Icon name="download" size={16} />
                {s.title}
              </a>
            ))}
            {n8n ? (
              <a className={styles.linkButton} href={`${n8n}/home/workflows`} target="_blank" rel="noreferrer">
                Open n8n <Icon name="external" size={14} style={{ verticalAlign: -2 }} />
              </a>
            ) : null}
          </div>
        </div>
      </details>
    </li>
  )
}
