import type { Metadata } from "next"
import type { ReactNode } from "react"
import { AppShell } from "@/components/shell/AppShell"
import "./globals.css"

export const metadata: Metadata = {
  title: "Symphony",
  description: "Private AI for small businesses",
}

// Applies a saved light/dark choice before first paint so the page never flashes.
const themeScript = `try{var t=localStorage.getItem("symphony-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600&family=Instrument+Sans:wght@400;600&display=swap"
        />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  )
}
