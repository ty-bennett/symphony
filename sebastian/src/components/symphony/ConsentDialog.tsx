"use client"

import { useState } from "react"
import { Button } from "@/components/ui/Button"
import { Dialog, Radio } from "@/components/ui/controls"
import { Icon } from "@/components/ui/Icon"
import { redactContactInfo } from "@/lib/symphony/routing"
import type { Place, RouteDecision, Settings } from "@/lib/symphony/types"
import styles from "./page.module.css"

export type ConsentChoice = { consent?: boolean; redact?: boolean; place?: Place }
type Option = "redact" | "send" | "private" | "device"

function Highlighted({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\[(?:email|phone) \d+\])/).map((piece, i) =>
        /^\[(?:email|phone) \d+\]$/.test(piece) ? (
          <span key={i} className={styles.redacted}>
            {piece}
          </span>
        ) : (
          piece
        ),
      )}
    </>
  )
}

// Shown when a job would send private information to the open cloud. Every
// option says plainly what it gives up.
export function ConsentDialog({
  decision,
  text,
  settings,
  busy,
  onCancel,
  onChoose,
}: {
  decision: RouteDecision | null
  text: string
  settings: Settings
  busy: boolean
  onCancel: () => void
  onChoose: (choice: ConsentChoice) => void
}) {
  const redacted = redactContactInfo(text)
  const hasContact = redacted.text !== text
  const web = decision?.reason.includes("web")
  const apps = decision?.apps?.length ? decision.apps.join(" and ") : null
  const options: { id: Option; title: string; detail: string }[] = []
  if (hasContact) {
    options.push({
      id: "redact",
      title: "Remove emails and phone numbers, then use the open cloud",
      detail: `Names and other details are sent as written${apps ? `, and what ${apps} sends back isn't removed` : ""}. Removed details are put back when you read the result.`,
    })
  }
  options.push({
    id: "send",
    title: "Use the open cloud as written",
    detail: `Everything in the request is sent${decision?.privateHits.length ? `, including ${decision.privateHits.join(" and ").toLowerCase()}` : ""}${apps ? `, and so is what ${apps} sends back` : ""}.`,
  })
  if (settings.places.private.enabled) {
    options.push({
      id: "private",
      title: "Keep it in your private cloud",
      detail: web ? "It can't search the web, so that part is skipped." : "It may be a little slower.",
    })
  }
  options.push({ id: "device", title: "Keep it on your Symphony", detail: "Most private. Results may be simpler." })

  const initial: Option = hasContact ? "redact" : settings.places.private.enabled ? "private" : "device"
  const [choice, setChoice] = useState<Option>(initial)
  const preview = choice === "redact" ? redacted.text : choice === "send" ? text : null

  function confirm() {
    if (choice === "redact") onChoose({ consent: true, redact: true, place: "open" })
    else if (choice === "send") onChoose({ consent: true, place: "open" })
    else onChoose({ place: choice })
  }

  return (
    <Dialog open={Boolean(decision)} onClose={onCancel} labelledBy="consent-title">
      <div className={styles.dialogBody}>
        <div className={styles.section}>
          <p className={styles.inline} style={{ color: "var(--open-text)", fontWeight: 600 }}>
            <Icon name="globe" size={16} />
            Open cloud
          </p>
          <h2 id="consent-title">This job would send private information out</h2>
          <p className={styles.muted}>
            It runs best in the open cloud because {decision?.reason}, but it includes{" "}
            {decision?.privateHits.join(" and ").toLowerCase() || "information you keep private"}.
          </p>
        </div>

        <fieldset className={styles.options}>
          <legend>How should Symphony do it?</legend>
          {options.map((o) => (
            <div key={o.id} className={choice === o.id ? `${styles.option} ${styles.optionOn}` : styles.option}>
              <Radio name="consent" checked={choice === o.id} onChange={() => setChoice(o.id)}>
                <span style={{ display: "flex", flexDirection: "column", padding: "8px 0" }}>
                  <span style={{ fontWeight: 600 }}>{o.title}</span>
                  <span className={styles.meta}>{o.detail}</span>
                </span>
              </Radio>
            </div>
          ))}
        </fieldset>

        {preview !== null ? (
          <div className={styles.section}>
            <h3 style={{ fontFamily: "var(--font-body)", fontSize: "var(--fs-body)" }}>What would be sent</h3>
            <p className={styles.preview}>
              <Highlighted text={preview} />
            </p>
          </div>
        ) : null}

        <div className={styles.buttons} style={{ justifyContent: "flex-end", borderTop: "1px solid var(--line)", paddingTop: 20 }}>
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" onClick={confirm} disabled={busy}>
            Start job
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
