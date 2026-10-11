"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Brand, ErrorNote, Field, message, postJson } from "./shared"
import styles from "./auth.module.css"

export function JoinView() {
  const [code, setCode] = useState<string | null>(null)
  const [valid, setValid] = useState<boolean | null>(null)
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("code") ?? ""
    setCode(c)
    if (!c) {
      setValid(false)
      return
    }
    fetch(`/api/auth/join?code=${encodeURIComponent(c)}`, { cache: "no-store" })
      .then((r) => r.json() as Promise<{ valid?: boolean }>)
      .then((b) => setValid(Boolean(b.valid)))
      .catch(() => setValid(false))
  }, [])

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await postJson("/api/auth/join", { code, name, email, password })
      window.location.assign("/")
    } catch (e) {
      setError(message(e))
      setBusy(false)
    }
  }

  return (
    <div className={styles.wrap}>
      <Brand />
      {valid === null ? (
        <p className={styles.muted}>Checking your invite</p>
      ) : !valid ? (
        <div className={styles.card}>
          <h1>This invite link doesn&apos;t work</h1>
          <p>It may have expired, been used already, or been cancelled. Invite links work once, for 7 days. Ask the owner for a new one.</p>
          <Link href="/login">Already have an account? Sign in</Link>
        </div>
      ) : (
        <form
          className={styles.card}
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <div>
            <h1>Join the team</h1>
            <p className={styles.muted}>You&apos;ll be able to start jobs, see the team&apos;s work, and approve your own jobs.</p>
          </div>
          <ErrorNote>{error}</ErrorNote>
          <div className={styles.form}>
            <Field label="Your name" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
            <Field label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            <Field
              label="Choose a password"
              hint="At least 8 characters."
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <Button type="submit" variant="primary" className={styles.full} disabled={busy || !name.trim() || !email || password.length < 8}>
            {busy ? "Joining" : "Join"}
          </Button>
        </form>
      )}
    </div>
  )
}
