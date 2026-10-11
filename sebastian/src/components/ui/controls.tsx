"use client"

import { useEffect, useRef, type ReactNode, type SelectHTMLAttributes } from "react"
import { Icon } from "./Icon"
import styles from "./ui.module.css"

export function Switch({
  checked,
  onChange,
  label,
  hideLabel,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  label: string
  hideLabel?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={hideLabel ? label : undefined}
      className={styles.switch}
      onClick={() => onChange(!checked)}
    >
      <span className={checked ? `${styles.track} ${styles.trackOn}` : styles.track} aria-hidden="true">
        <span className={styles.knob} />
      </span>
      {hideLabel ? null : label}
    </button>
  )
}

export function Checkbox({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (value: boolean) => void
  children: ReactNode
}) {
  return (
    <label className={styles.choice}>
      <input type="checkbox" className="sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className={styles.box} aria-hidden="true">
        <Icon name="check" size={12} strokeWidth={3.5} />
      </span>
      <span className={styles.choiceText}>{children}</span>
    </label>
  )
}

export function Radio({
  name,
  checked,
  onChange,
  children,
}: {
  name: string
  checked: boolean
  onChange: () => void
  children: ReactNode
}) {
  return (
    <label className={styles.choice}>
      <input type="radio" className="sr-only" name={name} checked={checked} onChange={onChange} />
      <span className={styles.dot} aria-hidden="true" />
      <span className={styles.choiceText}>{children}</span>
    </label>
  )
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={[styles.select, className].filter(Boolean).join(" ")}>
      <select {...rest}>{children}</select>
      <Icon name="chevronDown" size={16} className={styles.selectChevron} />
    </span>
  )
}

/** Accessible modal dialog built on <dialog>. */
export function Dialog({
  open,
  onClose,
  labelledBy,
  children,
}: {
  open: boolean
  onClose: () => void
  labelledBy: string
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])
  return (
    <dialog ref={ref} className={styles.dialog} aria-labelledby={labelledBy} onClose={onClose} onCancel={onClose}>
      {open ? children : null}
    </dialog>
  )
}
