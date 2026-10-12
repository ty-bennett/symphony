"use client"

import { Icon } from "@/components/ui/Icon"
import type { Part, ToolState } from "@/lib/opencode/types"
import styles from "./CodingAgent.module.css"

const OUTPUT_LIMIT = 4000

function truncate(text: string, limit = OUTPUT_LIMIT) {
  return text.length > limit ? `${text.slice(0, limit)}\n… ${text.length - limit} more characters` : text
}

function toolSummary(state?: ToolState): string {
  if (!state) return ""
  if (state.title) return state.title
  const input = state.input ?? {}
  for (const key of ["command", "filePath", "path", "pattern", "url", "description"]) {
    const value = input[key]
    if (typeof value === "string" && value) return value
  }
  return ""
}

function StatusIcon({ status }: { status?: ToolState["status"] }) {
  switch (status) {
    case "completed":
      return <Icon name="check" size={16} className={styles.statusDone} aria-label="Done" />
    case "error":
      return <Icon name="alert" size={16} className={styles.statusError} aria-label="Failed" />
    case "running":
      return <span className={styles.spinner} role="img" aria-label="Running" />
    default:
      return <Icon name="clock" size={16} className={styles.statusPending} aria-label="Waiting" />
  }
}

function ToolPart({ part }: { part: Part }) {
  const state = part.state
  const summary = toolSummary(state)
  const hasInput = state?.input && Object.keys(state.input).length > 0
  return (
    <details className={styles.tool}>
      <summary className={styles.toolSummary}>
        <StatusIcon status={state?.status} />
        <span className={styles.toolName}>{part.tool}</span>
        {summary ? <span className={styles.toolTitle}>{summary}</span> : null}
        <Icon name="chevronDown" size={16} className={styles.chevron} />
      </summary>
      <div className={styles.toolBody}>
        {hasInput ? (
          <>
            <p className={styles.toolLabel}>Input</p>
            <pre className={styles.code}>{truncate(JSON.stringify(state?.input, null, 2))}</pre>
          </>
        ) : null}
        {state?.status === "error" && state.error ? (
          <>
            <p className={styles.toolLabel}>Error</p>
            <pre className={`${styles.code} ${styles.codeError}`}>{truncate(state.error)}</pre>
          </>
        ) : null}
        {state?.output ? (
          <>
            <p className={styles.toolLabel}>Output</p>
            <pre className={styles.code}>{truncate(state.output)}</pre>
          </>
        ) : null}
        {!hasInput && !state?.output && !state?.error ? <p className={styles.toolLabel}>No details yet</p> : null}
      </div>
    </details>
  )
}

export function PartView({ part }: { part: Part }) {
  switch (part.type) {
    case "text":
      if (part.synthetic || part.ignored || !part.text) return null
      return <div className={styles.text}>{part.text}</div>
    case "reasoning":
      if (!part.text) return null
      return (
        <details className={styles.reasoning}>
          <summary>Thinking</summary>
          <div className={styles.text}>{part.text}</div>
        </details>
      )
    case "tool":
      return <ToolPart part={part} />
    case "file":
      return (
        <p className={styles.fileNote}>
          <Icon name="file" size={16} />
          {part.filename || part.url || "Attached file"}
        </p>
      )
    case "patch": {
      const files = part.files ?? []
      if (!files.length) return null
      const names = files.map((f) => f.split("/").pop()).join(", ")
      return (
        <p className={styles.fileNote}>
          <Icon name="file" size={16} />
          Edited {files.length} {files.length === 1 ? "file" : "files"}: {names}
        </p>
      )
    }
    default:
      // step-start, step-finish, snapshot, agent, retry, compaction and
      // not-yet-typed streaming parts render nothing.
      return null
  }
}
