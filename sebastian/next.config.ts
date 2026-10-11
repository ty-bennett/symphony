import { networkInterfaces } from "node:os"
import type { NextConfig } from "next"

// In `next dev`, let phones on the same network load the app by this
// computer's IP (e.g. http://192.168.1.20:3000). Next blocks dev resources
// from other origins unless they're listed.
const lanAddresses = Object.values(networkInterfaces())
  .flat()
  .filter((a) => a && a.family === "IPv4" && !a.internal)
  .map((a) => a!.address)

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: [...lanAddresses, "*.local"],
  async headers() {
    return [
      {
        // Always check for a new service worker.
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
    ]
  },
}

export default nextConfig
