"use client"

import { useCallback, useEffect, useState } from "react"
import { useMe } from "@/components/shell/AuthGate"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import type { InviteSummary, PublicUser } from "@/lib/auth/types"
import { api, relativeTime } from "@/lib/opencode/client"
import page from "@/components/symphony/page.module.css"

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))
const day = (ms: number) => new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" })

export function TeamView() {
  const { user: me } = useMe()
  const [people, setPeople] = useState<PublicUser[] | null>(null)
  const [invites, setInvites] = useState<InviteSummary[]>([])
  const [link, setLink] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [u, i] = await Promise.all([api<PublicUser[]>("/api/auth", "/users"), api<InviteSummary[]>("/api/auth", "/invites")])
      setPeople(u)
      setInvites(i)
    } catch (e) {
      setError(message(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  const invite = () =>
    run(async () => {
      const { code } = await api<{ code: string }>("/api/auth", "/invites", { method: "POST", body: "{}" })
      setLink(`${window.location.origin}/join?code=${encodeURIComponent(code)}`)
      setCopied(false)
    })

  async function copy() {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      // Clipboard needs HTTPS or localhost; select the text so it can be copied by hand.
      const input = document.getElementById("invite-link") as HTMLInputElement | null
      input?.select()
    }
  }

  const local = typeof window !== "undefined" && /^(localhost|127\.|\[::1\])/.test(window.location.hostname)

  return (
    <div className={`${page.page} ${page.narrow}`}>
      <header className={page.header}>
        <div className={page.headerText}>
          <h1>Team</h1>
          <p className={page.muted}>People who can start jobs. Only you can change privacy, spending, the team, and use the coding agent.</p>
        </div>
        <Button variant="primary" disabled={busy} onClick={() => void invite()}>
          Invite someone
        </Button>
      </header>

      {error ? (
        <div className={page.error} role="alert">
          <Icon name="alert" size={16} />
          <p style={{ flex: 1 }}>{error}</p>
          <Button variant="icon" aria-label="Dismiss" onClick={() => setError(null)}>
            <Icon name="close" size={16} />
          </Button>
        </div>
      ) : null}

      {link ? (
        <section className={page.section} aria-labelledby="link-h" style={{ padding: 20, borderRadius: "var(--radius-m)", background: "var(--raised)" }}>
          <h2 id="link-h" style={{ fontSize: "var(--fs-lead)" }}>
            Send this link to the person you&apos;re inviting
          </h2>
          <div className={page.inline}>
            <label htmlFor="invite-link" className="sr-only">
              Invite link
            </label>
            <input
              id="invite-link"
              readOnly
              className={page.textInput}
              value={link}
              onFocus={(e) => e.target.select()}
              style={{ flex: "1 1 280px", minWidth: 0, fontFamily: "var(--font-mono, monospace)", fontSize: "var(--fs-meta)" }}
            />
            <Button onClick={() => void copy()}>
              <Icon name={copied ? "check" : "copy"} size={16} />
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className={page.meta}>
            It works once, for 7 days, and lets them pick their own password. It&apos;s only shown now, so copy it before you leave this page.
            {local ? " This link starts with localhost, which only works on this computer. Open Symphony at its network address (like http://192.168.1.20:3000) and make the invite there." : ""}
          </p>
        </section>
      ) : null}

      <section className={page.section} aria-labelledby="people-h">
        <h2 id="people-h">
          People<span className={page.count}>{people?.length ?? ""}</span>
        </h2>
        {!people ? (
          <p className={page.muted}>Loading</p>
        ) : (
          <ul className={page.list}>
            {people.map((p) => (
              <li key={p.id} className={page.row}>
                <span className={page.rowText}>
                  <span className={page.rowTitle}>
                    {p.name}
                    {p.id === me?.id ? <span className={page.meta}> (you)</span> : null}
                  </span>
                  <span className={page.meta}>
                    {p.email} · {p.role === "owner" ? "Owner" : `Team member since ${day(p.created)}`}
                  </span>
                </span>
                {p.role === "owner" ? null : confirming === p.id ? (
                  <span className={page.inline}>
                    <span className={page.meta}>Remove {p.name.split(" ")[0]}? They&apos;ll be signed out.</span>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await api("/api/auth", `/users/${encodeURIComponent(p.id)}`, { method: "DELETE" })
                          setConfirming(null)
                        })
                      }
                    >
                      Remove
                    </Button>
                    <Button variant="text" onClick={() => setConfirming(null)}>
                      Cancel
                    </Button>
                  </span>
                ) : (
                  <Button variant="text" onClick={() => setConfirming(p.id)}>
                    Remove
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {people && people.length === 1 ? <p className={page.muted}>Just you so far. Invite someone to let them start jobs too.</p> : null}
      </section>

      {invites.length ? (
        <section className={page.section} aria-labelledby="invites-h">
          <h2 id="invites-h">
            Invites not used yet<span className={page.count}>{invites.length}</span>
          </h2>
          <ul className={page.list}>
            {invites.map((i) => (
              <li key={i.id} className={page.row}>
                <span className={page.rowText}>
                  <span>Made {relativeTime(i.created)}</span>
                  <span className={page.meta}>Works until {day(i.expires)}</span>
                </span>
                <Button
                  variant="text"
                  disabled={busy}
                  onClick={() => void run(() => api("/api/auth", `/invites/${encodeURIComponent(i.id)}`, { method: "DELETE" }))}
                >
                  Cancel invite
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className={page.section} aria-labelledby="forgot-h">
        <h2 id="forgot-h" style={{ fontSize: "var(--fs-lead)" }}>
          Someone forgot their password?
        </h2>
        <p className={page.muted}>Remove them and send a new invite. Their past jobs stay in Work.</p>
      </section>
    </div>
  )
}
