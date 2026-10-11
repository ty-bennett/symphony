"use client"

import { usePathname } from "next/navigation"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Icon, Logo } from "@/components/ui/Icon"
import styles from "./AppShell.module.css"

// Chrome/Edge/Android fire this when the app can be installed. Not in TS's DOM types.
type InstallPromptEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> }

const DISMISSED = "symphony-install-dismissed"

function wasDismissed() {
  try {
    return localStorage.getItem(DISMISSED) === "1"
  } catch {
    return false
  }
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true
}

function isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
}

/**
 * Offers to put Symphony on the home screen. iPhones and iPads have no
 * install button for websites, so there it explains the Share step; browsers
 * that support installing get a real Install button. Hidden once installed or
 * dismissed, and on the coding agent, which needs the whole screen.
 */
export function InstallHint() {
  const pathname = usePathname()
  const [mode, setMode] = useState<"ios" | "prompt" | null>(null)
  const prompt = useRef<InstallPromptEvent | null>(null)

  useEffect(() => {
    // Shows a friendly page when the box can't be reached. Needs HTTPS (or localhost).
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {})
    }
    if (isStandalone() || wasDismissed()) return
    if (isIOS()) setMode("ios")

    const onPrompt = (event: Event) => {
      event.preventDefault()
      prompt.current = event as InstallPromptEvent
      setMode("prompt")
    }
    const onInstalled = () => setMode(null)
    window.addEventListener("beforeinstallprompt", onPrompt)
    window.addEventListener("appinstalled", onInstalled)
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt)
      window.removeEventListener("appinstalled", onInstalled)
    }
  }, [])

  function dismiss() {
    setMode(null)
    try {
      localStorage.setItem(DISMISSED, "1")
    } catch {
      // Private mode: it just comes back next visit.
    }
  }

  async function install() {
    const event = prompt.current
    if (!event) return
    await event.prompt()
    const { outcome } = await event.userChoice
    prompt.current = null
    if (outcome === "accepted") setMode(null)
  }

  if (!mode || pathname.startsWith("/coding-agent")) return null

  return (
    <div className={styles.installWrap}>
      <section className={styles.install} aria-label="Install Symphony">
        <span className={styles.installIcon} aria-hidden="true">
          <Logo size={26} />
        </span>
        <p className={styles.installText}>
          {mode === "ios" ? (
            <>
              <strong>Get the Symphony app.</strong> Tap Share{" "}
              <Icon name="share" size={16} className={styles.installShare} />, then <strong>Add to Home Screen</strong>.
            </>
          ) : (
            <>
              <strong>Install Symphony as an app.</strong> It opens in its own window, with an icon on your home screen.
            </>
          )}
        </p>
        <div className={styles.installActions}>
          {mode === "prompt" ? (
            <Button variant="primary" onClick={() => void install()}>
              Install
            </Button>
          ) : null}
          <Button variant="icon" aria-label="Not now" onClick={dismiss}>
            <Icon name="close" size={16} />
          </Button>
        </div>
      </section>
    </div>
  )
}
