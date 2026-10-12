"use client"

import { useCallback, useEffect, useState } from "react"
import { useMe } from "@/components/shell/AuthGate"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import type { TeamMember } from "@/lib/auth/types"
import { api } from "@/lib/opencode/client"
import page from "@/components/symphony/page.module.css"

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

const status = (p: TeamMember) =>
  [p.role === "owner" ? "Owner" : "Team member", p.invited ? "Invited, hasn't signed in yet" : null, p.signIn === "sso" ? "Company sign-in" : null]
    .filter(Boolean)
    .join(" · ")

export function TeamView() {
  const { user: me } = useMe()
  const [people, setPeople] = useState<TeamMember[] | null>(null)
  const [email, setEmail] = useState("")
  const [name, setName] = useState("")
  const [sent, setSent] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { users } = await api<{ users: TeamMember[] }>("/api/auth", "/users")
      setPeople(users.filter((u) => u.enabled))
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
      const to = email.trim()
      await api("/api/auth", "/users", { method: "POST", body: JSON.stringify({ email: to, name: name.trim() || undefined }) })
      setSent(to)
      setEmail("")
      setName("")
    })

  // The signed-in user's id is their Cognito sub, the same id the Team API uses.
  const isMe = (p: TeamMember) => p.id === me?.id

  return (
    <div className={`${page.page} ${page.narrow}`}>
      <header className={page.header}>
        <div className={page.headerText}>
          <h1>Team</h1>
          <p className={page.muted}>People who can start jobs. Only you can change privacy, spending, the team, and use the coding agent.</p>
        </div>
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

      <section className={page.section} aria-labelledby="invite-h" style={{ padding: 20, borderRadius: "var(--radius-m)", background: "var(--raised)" }}>
        <h2 id="invite-h" style={{ fontSize: "var(--fs-lead)" }}>
          Invite someone
        </h2>
        <form
          className={page.inline}
          onSubmit={(e) => {
            e.preventDefault()
            void invite()
          }}
        >
          <label htmlFor="invite-email" className="sr-only">
            Email
          </label>
          <input
            id="invite-email"
            type="email"
            required
            placeholder="Email"
            autoComplete="off"
            className={page.textInput}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ flex: "2 1 220px", minWidth: 0 }}
          />
          <label htmlFor="invite-name" className="sr-only">
            Name (optional)
          </label>
          <input
            id="invite-name"
            placeholder="Name (optional)"
            autoComplete="off"
            className={page.textInput}
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ flex: "1 1 160px", minWidth: 0 }}
          />
          <Button type="submit" variant="primary" disabled={busy || !email.trim()}>
            Send invite
          </Button>
        </form>
        <p className={page.meta} aria-live="polite">
          {sent
            ? `Invitation sent to ${sent}. They'll get an email with how to sign in.`
            : "They'll get an email with how to sign in. If your company uses its own sign-in, they use that."}
        </p>
      </section>

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
                    {isMe(p) ? <span className={page.meta}> (you)</span> : null}
                  </span>
                  <span className={page.meta}>
                    {p.email} · {status(p)}
                  </span>
                </span>
                {p.role === "owner" || isMe(p) ? null : confirming === p.id ? (
                  <span className={page.inline}>
                    <span className={page.meta}>Remove {p.name.split(" ")[0]}? They&apos;ll be signed out everywhere.</span>
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
    </div>
  )
}
