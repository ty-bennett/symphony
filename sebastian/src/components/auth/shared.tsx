"use client"

import { useId, type InputHTMLAttributes, type ReactNode } from "react"
import { Icon, Logo } from "@/components/ui/Icon"
import styles from "./auth.module.css"

export function Brand() {
  return (
    <p className={styles.brand}>
      <Logo size={34} />
      Symphony
    </p>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <div className={styles.error} role="alert">
      <Icon name="alert" size={16} />
      <p>{children}</p>
    </div>
  )
}

export function Field({ label, hint, ...input }: { label: string; hint?: string } & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId()
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <input id={id} className={styles.input} aria-describedby={hint ? `${id}-hint` : undefined} {...input} />
      {hint ? (
        <span id={`${id}-hint`} className={styles.hint}>
          {hint}
        </span>
      ) : null}
    </div>
  )
}

/** POST JSON and return the body, or throw with the server's plain-language error. */
export async function postJson<T>(url: string, body: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
  } catch {
    throw new Error("Can't reach Symphony. Check that you're on the shop's network.")
  }
  const data = (await response.json().catch(() => ({}))) as T & { error?: string }
  if (!response.ok) throw new Error(data.error ?? `Something went wrong (${response.status}).`)
  return data
}

/** Only follow ?next= to a page on this site. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/"
}

export const message = (e: unknown) => (e instanceof Error ? e.message : String(e))
