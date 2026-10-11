import type { ButtonHTMLAttributes } from "react"
import styles from "./ui.module.css"

type Variant = "primary" | "secondary" | "text" | "icon"

// One "primary" per screen; everything else is secondary, text or icon.
export function Button({
  variant = "secondary",
  className,
  type = "button",
  ...rest
}: { variant?: Variant } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} className={[styles.button, styles[variant], className].filter(Boolean).join(" ")} {...rest} />
}
