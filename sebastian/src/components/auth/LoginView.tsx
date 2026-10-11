"use client"

import { useState } from "react"
import { Button } from "@/components/ui/Button"
import { Brand, ErrorNote, Field, message, postJson, safeNext } from "./shared"
import styles from "./auth.module.css"

export function LoginView() {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await postJson("/api/auth/login", { email, password })
      // A full load, so every screen starts with the new account.
      window.location.assign(safeNext(new URLSearchParams(window.location.search).get("next")))
    } catch (e) {
      setError(message(e))
      setBusy(false)
    }
  }

  return (
    <div className={styles.wrap}>
      <Brand />
      <form
        className={styles.card}
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <h1>Sign in</h1>
        <ErrorNote>{error}</ErrorNote>
        <div className={styles.form}>
          <Field label="Email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <Field
            label="Password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <Button type="submit" variant="primary" className={styles.full} disabled={busy || !email || !password}>
          {busy ? "Signing in" : "Sign in"}
        </Button>
      </form>
      <p className={styles.footnote}>
        New to the team? Ask the owner for an invite link. Forgot your password? The owner can remove you and send a new invite.
      </p>
    </div>
  )
}
