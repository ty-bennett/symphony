"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react"
import { EmptyState } from "@/components/ui/EmptyState"
import { OWNER_ONLY_PATHS, PUBLIC_PATHS, type Me, type PublicUser } from "@/lib/auth/types"

type MeState = { me: Me | null; user: PublicUser | null; refresh: () => Promise<void> }

const MeContext = createContext<MeState>({ me: null, user: null, refresh: async () => {} })

/** Who is signed in (null while loading or signed out). */
export function useMe() {
  return useContext(MeContext)
}

const matches = (path: string, list: string[]) => list.some((p) => path === p || path.startsWith(`${p}/`))
export const isPublicPath = (path: string) => matches(path, PUBLIC_PATHS)

/**
 * Sends people where they need to be: first run to /setup, signed-out to
 * /login, an owner who hasn't finished setup back to /setup. The API enforces
 * the same rules; this just avoids showing pages that would only say no.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [me, setMe] = useState<Me | null>(null)

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/auth/me", { cache: "no-store" })
      setMe((await response.json()) as Me)
    } catch {
      setMe({ state: "signed-out" })
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const publicPage = isPublicPath(pathname)
  useEffect(() => {
    if (!me) return
    if (me.state === "setup" && pathname !== "/setup") router.replace("/setup")
    else if (me.state === "signed-out" && !publicPage) {
      const next = `${pathname}${typeof window === "undefined" ? "" : window.location.search}`
      router.replace(next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`)
    } else if (me.state === "signed-out" && pathname === "/setup") {
      router.replace("/login")
    } else if (me.state === "signed-in") {
      const needsSetup = me.user.role === "owner" && !me.onboarded
      if (needsSetup && pathname !== "/setup") router.replace("/setup")
      else if (!needsSetup && (pathname === "/login" || pathname === "/join" || pathname === "/setup")) router.replace("/")
    }
  }, [me, pathname, publicPage, router])

  const user = me?.state === "signed-in" ? me.user : null
  let content: ReactNode = null
  if (publicPage) content = children
  else if (user && user.role !== "owner" && matches(pathname, OWNER_ONLY_PATHS)) {
    content = (
      <EmptyState title="Only the owner can open this" action={<Link href="/">Back to Today</Link>}>
        <p>Ask the owner if you need something changed here.</p>
      </EmptyState>
    )
  } else if (user) content = children

  return <MeContext.Provider value={{ me, user, refresh }}>{content}</MeContext.Provider>
}
