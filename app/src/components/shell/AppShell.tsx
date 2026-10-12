"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useState, type ReactNode } from "react"
import { Icon, Logo, type IconName } from "@/components/ui/Icon"
import type { PublicUser } from "@/lib/auth/types"
import { AuthGate, isPublicPath, useMe } from "./AuthGate"
import { InstallHint } from "./InstallHint"
import { ThemeToggle } from "./ThemeToggle"
import styles from "./AppShell.module.css"

const nav: { href: string; label: string; icon: IconName; ownerOnly?: boolean }[] = [
  { href: "/", label: "Today", icon: "today" },
  { href: "/work", label: "Work", icon: "work" },
  { href: "/coding-agent", label: "Coding agent", icon: "code", ownerOnly: true },
  { href: "/team", label: "Team", icon: "users", ownerOnly: true },
  { href: "/privacy", label: "Privacy and spending", icon: "shield", ownerOnly: true },
]

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <Frame>{children}</Frame>
    </AuthGate>
  )
}

function Frame({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const { user } = useMe()

  // Sign-in, join and setup pages stand on their own.
  if (isPublicPath(pathname)) return <main className={styles.bare}>{children}</main>

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <Link href="/" className={styles.brand}>
          <Logo />
          <span className={styles.brandName}>Symphony</span>
        </Link>
        <nav aria-label="Main">
          <ul className={styles.nav}>
            {nav
              .filter((item) => !item.ownerOnly || user?.role === "owner")
              .map((item) => {
                const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      title={item.label}
                      className={active ? `${styles.navLink} ${styles.navActive}` : styles.navLink}
                    >
                      <Icon name={item.icon} size={18} />
                      <span className={styles.navLabel}>{item.label}</span>
                    </Link>
                  </li>
                )
              })}
          </ul>
        </nav>
        <div className={styles.footer}>{user ? <AccountMenu user={user} /> : <ThemeToggle />}</div>
      </aside>
      <main className={styles.main}>
        <InstallHint />
        {children}
      </main>
    </div>
  )
}

function AccountMenu({ user }: { user: PublicUser }) {
  const [busy, setBusy] = useState(false)

  async function signOut() {
    setBusy(true)
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {})
    window.location.assign("/login")
  }

  return (
    <details className={styles.account}>
      <summary className={styles.navLink} aria-label={`Account: ${user.name}`}>
        <span className={styles.avatar} aria-hidden="true">
          {user.name.charAt(0).toUpperCase()}
        </span>
        <span className={styles.accountName}>
          <span>{user.name}</span>
          <span className={styles.accountRole}>{user.role === "owner" ? "Owner" : "Team member"}</span>
        </span>
      </summary>
      <div className={styles.menu}>
        <p className={styles.menuWho}>
          <strong>{user.name}</strong>
          <span>{user.email}</span>
        </p>
        <ThemeToggle />
        <button type="button" className={styles.navLink} disabled={busy} onClick={() => void signOut()}>
          <Icon name="logout" size={18} />
          Sign out
        </button>
      </div>
    </details>
  )
}
