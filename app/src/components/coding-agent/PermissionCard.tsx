"use client"

import { useState } from "react"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import type { Permission } from "@/lib/opencode/types"
import styles from "./CodingAgent.module.css"

export function PermissionCard({
  permission,
  onAnswer,
}: {
  permission: Permission
  onAnswer: (response: "once" | "always" | "reject") => Promise<void>
}) {
  const [pending, setPending] = useState(false)

  async function answer(response: "once" | "always" | "reject") {
    setPending(true)
    try {
      await onAnswer(response)
    } finally {
      setPending(false)
    }
  }

  return (
    <section className={styles.permission} aria-label="Approval needed">
      <p className={styles.permissionKicker}>
        <Icon name="alert" size={16} />
        Needs your OK
      </p>
      <h3 className={styles.permissionTitle}>{permission.title}</h3>
      {permission.details.length ? (
        <ul className={styles.permissionDetails}>
          {permission.details.map((d) => (
            <li key={d}>
              <code>{d}</code>
            </li>
          ))}
        </ul>
      ) : null}
      <div className={styles.permissionActions}>
        <Button variant="primary" disabled={pending} onClick={() => answer("once")}>
          Allow once
        </Button>
        <Button variant="secondary" disabled={pending} onClick={() => answer("always")}>
          Always allow
        </Button>
        <Button variant="text" disabled={pending} onClick={() => answer("reject")}>
          Deny
        </Button>
      </div>
    </section>
  )
}
