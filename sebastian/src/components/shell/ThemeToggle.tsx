"use client"

import { useEffect, useState } from "react"
import { Icon } from "@/components/ui/Icon"
import styles from "./AppShell.module.css"

type Theme = "light" | "dark"

function currentTheme(): Theme {
  const explicit = document.documentElement.dataset.theme
  if (explicit === "light" || explicit === "dark") return explicit
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null)

  useEffect(() => setTheme(currentTheme()), [])

  function toggle() {
    const next: Theme = currentTheme() === "dark" ? "light" : "dark"
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem("symphony-theme", next)
    } catch {
      // Storage can be unavailable (private mode); the choice still applies for this visit.
    }
    setTheme(next)
  }

  return (
    <button
      type="button"
      className={styles.navLink}
      aria-label={theme === "dark" ? "Light theme" : "Dark theme"}
      onClick={toggle}
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
      <span className={styles.toggleLabel}>{theme === "dark" ? "Light theme" : "Dark theme"}</span>
    </button>
  )
}
