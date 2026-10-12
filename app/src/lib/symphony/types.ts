// Shared Symphony types (client and server).

/** Where a job runs: Soloist (the model on the box) or Orchestra (the agent in the private cloud). */
export type Place = "device" | "private"

export type PrivacyCategory = {
  id: string
  label: string
  /** Lower-case words or phrases that mark a request as touching this category. */
  keywords: string[]
  on: boolean
  custom?: boolean
}

export type Settings = {
  businessName: string
  businessType: string
  /** The owner finished first-run setup. */
  onboarded: boolean
  checkFirst: boolean
  keepPrivate: PrivacyCategory[]
  places: {
    device: { model: string }
    private: { enabled: boolean; model: string }
  }
  spending: { limit: number; atLimit: "pause" | "device-only" }
  /** What "saved" is measured against: "typical" frontier prices or a priced "provider/model". */
  savings: { compareWith: string }
}

export type RouteDecision = {
  place: Place
  /** Plain-language reason, written to follow "because". */
  reason: string
  /** Names of keep-private categories the request touches. */
  privateHits: string[]
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
  /** Who started it, when it was started from Symphony. */
  by?: { id: string; name: string; owner?: boolean }
  tokens?: Tokens
  /** What the same tokens would have cost at frontier-model prices (see Rates). */
  wouldHaveCost?: number
  /** wouldHaveCost minus what the job actually cost. 0 for jobs that ran outside Symphony. */
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
  /** This month: what Soloist and Orchestra jobs would have cost at frontier prices, what they cost, and the difference. */
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
  /** Who started the job (user id). */
  by?: string
}
