import type { Place } from "./types"

/** color: the official place color (icons, bars, swatches). ink: the same hue, readable as text. */
export const PLACES: Record<Place, { name: string; short: string; promise: string; color: string; ink: string; icon: "house" | "lock" }> = {
  device: {
    name: "Soloist",
    short: "on Soloist",
    promise: "The model on the Symphony box in your back office. Nothing leaves the building, and it costs nothing.",
    color: "var(--device)",
    ink: "var(--device-text)",
    icon: "house",
  },
  private: {
    name: "Orchestra",
    short: "on Orchestra",
    promise: "Symphony's agent in your own private cloud account, for big jobs and the web. Never used to train AI, and nothing is kept.",
    color: "var(--private)",
    ink: "var(--private-text)",
    icon: "lock",
  },
}

export const PLACE_ORDER: Place[] = ["device", "private"]

// Providers that run locally on the box or inside the owner's own cloud account.
const LOCAL_PROVIDERS = ["ollama", "lmstudio", "llama.cpp", "llamacpp", "local", "symphony", "vllm"]
const PRIVATE_PROVIDERS = ["amazon-bedrock", "azure", "google-vertex", "google-vertex-anthropic"]

/** Best guess at where a model runs, for jobs started outside Symphony; null when it is neither Soloist nor Orchestra. */
export function placeForProvider(providerID: string | undefined, configured?: Partial<Record<Place, string>>): Place | null {
  if (!providerID) return null
  for (const place of PLACE_ORDER) {
    const model = configured?.[place]
    if (model && model.split("/")[0] === providerID) return place
  }
  if (LOCAL_PROVIDERS.includes(providerID)) return "device"
  if (PRIVATE_PROVIDERS.includes(providerID)) return "private"
  // Any other provider runs outside Symphony (coding agent sessions only).
  return null
}

export function formatCost(cost: number): string {
  if (!cost) return "$0.00"
  if (cost < 0.01) return "under 1¢"
  return `$${cost.toFixed(2)}`
}
