"use client"

import { useMemo, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import { diffRows } from "@/lib/opencode/diff"
import type { FileDiff, Todo } from "@/lib/opencode/types"
import styles from "./CodingAgent.module.css"

function FileRows({ file }: { file: FileDiff }) {
  const result = useMemo(() => diffRows(file), [file])
  if ("tooLarge" in result) return <p className={styles.diffNote}>This file is too large to show here.</p>
  if (!result.rows.length) return <p className={styles.diffNote}>No line changes to show.</p>
  return (
    <div className={styles.diffTable} role="table" aria-label={`Changes in ${file.file ?? "file"}`}>
      {result.rows.map((row, i) =>
        row.kind === "gap" ? (
          <div key={i} className={styles.diffGap} role="row">
            <span role="cell">{row.count ? `${row.count} unchanged ${row.count === 1 ? "line" : "lines"}` : row.header ?? "…"}</span>
          </div>
        ) : (
          <div key={i} className={`${styles.diffRow} ${styles[`diff_${row.kind}`]}`} role="row">
            <span className={styles.diffNo} role="cell">
              {row.kind === "add" ? "" : row.oldNo}
            </span>
            <span className={styles.diffNo} role="cell">
              {row.kind === "del" ? "" : row.newNo}
            </span>
            <span className={styles.diffSign} role="cell" aria-label={row.kind === "add" ? "added" : row.kind === "del" ? "removed" : undefined}>
              {row.kind === "add" ? "+" : row.kind === "del" ? "−" : ""}
            </span>
            <code className={styles.diffText} role="cell">
              {row.text || " "}
            </code>
          </div>
        ),
      )}
    </div>
  )
}

function FileEntry({ file }: { file: FileDiff }) {
  const [open, setOpen] = useState(false)
  const path = file.file ?? "(unknown file)"
  const slash = path.lastIndexOf("/")
  return (
    <li className={styles.diffFile}>
      <button type="button" className={styles.diffFileButton} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={16} className={styles.chevronMuted} />
        <span className={styles.diffPath} title={path}>
          {slash >= 0 ? <span className={styles.diffDir}>{path.slice(0, slash + 1)}</span> : null}
          {path.slice(slash + 1)}
        </span>
        <span className={styles.diffCounts}>
          <span className={styles.adds}>+{file.additions}</span>
          <span className={styles.dels}>−{file.deletions}</span>
        </span>
      </button>
      {open ? <FileRows file={file} /> : null}
    </li>
  )
}

export function DiffPanel({
  diff,
  todos,
  hasSession,
  onClose,
}: {
  diff: FileDiff[] | null
  todos: Todo[]
  hasSession: boolean
  onClose: () => void
}) {
  const totals = (diff ?? []).reduce(
    (t, f) => ({ add: t.add + (f.additions || 0), del: t.del + (f.deletions || 0) }),
    { add: 0, del: 0 },
  )
  return (
    <aside className={styles.diffPanel} aria-label="Changes">
      <div className={styles.diffHead}>
        <h2>Changes</h2>
        {diff?.length ? (
          <span className={styles.diffCounts}>
            <span className={styles.adds}>+{totals.add}</span>
            <span className={styles.dels}>−{totals.del}</span>
          </span>
        ) : null}
        <Button variant="icon" aria-label="Hide changes" onClick={onClose}>
          <Icon name="close" size={16} />
        </Button>
      </div>

      {todos.length ? (
        <section className={styles.todos} aria-label="Agent's to-do list">
          <h3>To-do</h3>
          <ul>
            {todos.map((t, i) => (
              <li key={t.id ?? i} className={styles[`todo_${t.status}`] ?? undefined}>
                <Icon
                  name={t.status === "completed" ? "check" : t.status === "in_progress" ? "chevronRight" : "clock"}
                  size={16}
                />
                {t.content}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!hasSession ? (
        <p className={styles.diffNote}>Changes the agent makes show up here.</p>
      ) : diff === null ? (
        <p className={styles.diffNote}>Loading changes</p>
      ) : diff.length === 0 ? (
        <p className={styles.diffNote}>No file changes yet.</p>
      ) : (
        <ul className={styles.diffFiles}>
          {diff.map((f, i) => (
            <FileEntry key={f.file ?? i} file={f} />
          ))}
        </ul>
      )}
    </aside>
  )
}
