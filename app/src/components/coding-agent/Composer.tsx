"use client"

import { useRef, useState, type KeyboardEvent } from "react"
import { Button } from "@/components/ui/Button"
import { Icon } from "@/components/ui/Icon"
import type { ProvidersResponse } from "@/lib/opencode/types"
import styles from "./CodingAgent.module.css"

export function Composer({
  busy,
  quiet,
  providers,
  model,
  onModelChange,
  onSend,
  onStop,
  placeholder,
}: {
  busy: boolean
  /** Another action (an approval) is the main one right now. */
  quiet: boolean
  providers: ProvidersResponse | null
  model: string
  onModelChange: (value: string) => void
  onSend: (text: string) => Promise<boolean>
  onStop: () => void
  placeholder: string
}) {
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)

  async function submit() {
    const value = text.trim()
    if (!value || sending || busy) return
    setSending(true)
    const ok = await onSend(value)
    setSending(false)
    if (ok) setText("")
    input.current?.focus()
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void submit()
    }
  }

  const modelKnown =
    !model || providers?.providers.some((p) => model.startsWith(`${p.id}/`) && p.models[model.slice(p.id.length + 1)])

  return (
    <form
      className={styles.composer}
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <label htmlFor="agent-input" className="sr-only">
        Message the coding agent
      </label>
      <textarea
        id="agent-input"
        ref={input}
        className={styles.input}
        rows={2}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className={styles.composerBar}>
        <label className={styles.modelPicker}>
          <span className="sr-only">Model</span>
          <select value={modelKnown ? model : ""} onChange={(e) => onModelChange(e.target.value)} disabled={!providers}>
            <option value="">Default model</option>
            {providers?.providers.map((p) => (
              <optgroup key={p.id} label={p.name}>
                {Object.values(p.models).map((m) => (
                  <option key={m.id} value={`${p.id}/${m.id}`}>
                    {m.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <Icon name="chevronDown" size={16} className={styles.selectChevron} />
        </label>
        <span className={styles.hint}>Shift+Enter for a new line</span>
        {busy ? (
          <Button variant="secondary" onClick={onStop}>
            <Icon name="stop" size={16} />
            Stop
          </Button>
        ) : (
          <Button type="submit" variant={quiet ? "secondary" : "primary"} disabled={!text.trim() || sending}>
            Send
          </Button>
        )}
      </div>
    </form>
  )
}
