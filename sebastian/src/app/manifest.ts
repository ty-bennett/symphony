import type { MetadataRoute } from "next"

// Lets phones install Symphony from the browser ("Add to Home Screen") and
// open it full screen, like an app.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Symphony",
    short_name: "Symphony",
    description: "Private AI for small businesses",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#ffffed",
    theme_color: "#f7f7e3",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  }
}
