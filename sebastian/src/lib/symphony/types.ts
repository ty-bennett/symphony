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
  /** What "saved" is measured against: "open" (the open-cloud model), "typical", or a "provider/model". */
  savings: { compareWith: string }
  /** Connected apps (through n8n): whether jobs may use them, and whether running an app action always asks first. */
  apps: { enabled: boolean; askFirst: boolean }
}

export type RouteDecision = {
  place: Place
  /** Plain-language reason, written to follow "because". */
  reason: string
  /** Names of keep-private categories the request touches. */
  privateHits: string[]
  /** True when the job would send private information to the open cloud. */
  needsConsent: boolean
  /** Connected apps the request mentions (their data counts as private). */
  apps?: string[]
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
  tokens?: Tokens
  /** What the same tokens would have cost in the open cloud (see Rates). */
  wouldHaveCost?: number
  /** wouldHaveCost minus what the job actually cost. 0 for open-cloud jobs. */
  saved?: number
}

export type Tokens = { input: number; output: number; reasoning: number; cacheRead: number; cacheWrite: number }

/** Prices in USD per million tokens. */
export type Rates = {
  label: string
  model?: string
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
  typical: boolean
}

export type Savings = {
  month: string
  /** This month: what jobs kept off the open cloud would have cost there, what they cost, and the difference. */
  wouldHaveCost: number
  actual: number
  saved: number
  byPlace: Record<Place, number>
  jobs: number
  allTime: number
  rates: Rates
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
