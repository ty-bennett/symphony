// Shared Symphony types (client and server).

/** The three places a job can run, from most to least private. */
export type Place = "device" | "private" | "open"

export type PrivacyCategory = {
  id: string
  label: string
  /** Lower-case words or phrases that mark a request as touching this category. */
  keywords: string[]
  on: boolean
  custom?: boolean
}

export type OpenCloudRule = "ask-if-private" | "ask-always" | "never"

export type Settings = {
  businessType: string
  checkFirst: boolean
  keepPrivate: PrivacyCategory[]
  places: {
    device: { model: string }
    private: { enabled: boolean; model: string }
    open: { enabled: boolean; model: string; rule: OpenCloudRule }
  }
  spending: { limit: number; atLimit: "pause" | "device-only" }
}

export type RouteDecision = {
  place: Place
  /** Plain-language reason, written to follow "because". */
  reason: string
  /** Names of keep-private categories the request touches. */
  privateHits: string[]
  /** True when the job would send private information to the open cloud. */
  needsConsent: boolean
}

export type JobStatus = "needs-ok" | "working" | "done" | "undone"

export type Job = {
  id: string
  title: string
  created: number
  updated: number
  status: JobStatus
  place: Place | null
  reason?: string
  model?: string
  checkFirst?: boolean
  cost: number
  approvals: number
}

export type Spending = {
  month: string
  total: number
  byPlace: Record<Place, number>
  limit: number
  atLimit: Settings["spending"]["atLimit"]
}

/** Stored on the opencode session's metadata under `symphony`. */
export type JobMeta = {
  place: Place
  reason: string
  checkFirst: boolean
  /** Placeholder -> original value, for text that was redacted before leaving the device. */
  redactions?: Record<string, string>
}
