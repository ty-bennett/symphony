"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Brand, ErrorNote, safeNext } from "./shared"
import styles from "./auth.module.css"

/** Sign-in happens on the company's Cognito login page (or its SSO); this just sends people there. */
export function LoginView() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [next, setNext] = useState("/")

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    setError(params.get("error"))
    setNext(safeNext(params.get("next")))
  }, [])

  function signIn() {
    setBusy(true)
    window.location.assign(`/api/auth/login?next=${encodeURIComponent(next)}`)
  }

  return (
    <div className={styles.wrap}>
      <Brand />
      <div className={styles.card}>
        <h1>Sign in</h1>
        <ErrorNote>{error}</ErrorNote>
        <Button variant="primary" className={styles.full} disabled={busy} onClick={signIn}>
          {busy ? "Signing in" : "Sign in"}
        </Button>
      </div>
      <p className={styles.footnote}>New to the team? Ask the owner to invite you. The invitation arrives by email.</p>
    </div>
  )
}
