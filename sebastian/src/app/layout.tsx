import type { Metadata, Viewport } from "next"
import type { ReactNode } from "react"
import { AppShell } from "@/components/shell/AppShell"
import "./globals.css"

export const metadata: Metadata = {
  title: { default: "Symphony", template: "%s · Symphony" },
  description: "Private AI for small businesses",
  applicationName: "Symphony",
  // iPhone/iPad: open full screen from the Home Screen, named "Symphony".
  appleWebApp: { capable: true, title: "Symphony", statusBarStyle: "default" },
  formatDetection: { telephone: false },
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Draw edge to edge; the shell pads for the notch and home bar.
  viewportFit: "cover",
  // Matches the top bar so the status bar blends in. ThemeToggle updates it.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafa" },
    { media: "(prefers-color-scheme: dark)", color: "#141414" },
  ],
}

// Applies a saved light/dark choice before first paint so the page never flashes.
const themeScript = `try{var t=localStorage.getItem("symphony-theme");if(t==="light"||t==="dark"){document.documentElement.dataset.theme=t;document.querySelectorAll('meta[name="theme-color"]').forEach(function(m){m.content=t==="dark"?"#141414":"#fafafa"})}}catch(e){}`

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
