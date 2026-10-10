"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import type { ReactNode } from "react"
import { Icon, Logo, type IconName } from "@/components/ui/Icon"
import { ThemeToggle } from "./ThemeToggle"
import styles from "./AppShell.module.css"

const nav: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Today", icon: "today" },
  { href: "/work", label: "Work", icon: "work" },
  { href: "/coding-agent", label: "Coding agent", icon: "code" },
  { href: "/privacy", label: "Privacy and spending", icon: "shield" },
]

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <Link href="/" className={styles.brand}>
          <Logo />
          <span className={styles.brandName}>Symphony</span>
        </Link>
        <nav aria-label="Main">
          <ul className={styles.nav}>
            {nav.map((item) => {
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
        <div className={styles.footer}>
          <ThemeToggle />
        </div>
      </aside>
      <main className={styles.main}>{children}</main>
    </div>
  )
}
