import type { Place } from "./types"

export const PLACES: Record<Place, { name: string; short: string; promise: string; color: string; icon: "house" | "lock" | "globe" }> = {
  device: {
    name: "On your Symphony",
    short: "on your Symphony",
    promise: "The box in your back office. Nothing leaves the building, and it costs nothing.",
    color: "var(--device)",
    icon: "house",
  },
  private: {
    name: "Private cloud",
    short: "in your private cloud",
    promise: "Your own encrypted cloud account. Never used to train AI.",
    color: "var(--private)",
    icon: "lock",
  },
  open: {
    name: "Open cloud",
    short: "in the open cloud",
    promise: "The most capable AI, and it can search the web.",
    color: "var(--open)",
    icon: "globe",
  },
}

export const PLACE_ORDER: Place[] = ["device", "private", "open"]

// Providers that run locally on the box or inside the owner's own cloud account.
const LOCAL_PROVIDERS = ["ollama", "lmstudio", "llama.cpp", "llamacpp", "local", "symphony", "vllm"]
const PRIVATE_PROVIDERS = ["amazon-bedrock", "azure", "google-vertex", "google-vertex-anthropic"]

/** Best guess at where a model runs, for jobs started outside Symphony. */
export function placeForProvider(providerID: string | undefined, configured?: Partial<Record<Place, string>>): Place | null {
  if (!providerID) return null
  for (const place of PLACE_ORDER) {
    const model = configured?.[place]
    if (model && model.split("/")[0] === providerID) return place
  }
  if (LOCAL_PROVIDERS.includes(providerID)) return "device"
  if (PRIVATE_PROVIDERS.includes(providerID)) return "private"
  return "open"
}

export function formatCost(cost: number): string {
  if (!cost) return "$0.00"
  if (cost < 0.01) return "under 1¢"
  return `$${cost.toFixed(2)}`
}
