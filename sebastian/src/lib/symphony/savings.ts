// "Theoretically saved": what the jobs Symphony kept off the open cloud would
// have cost there, priced from the tokens each job actually used, minus what
// they really cost. Shared by server and browser; no I/O here.
import type { Job, Place, Rates, Savings, Tokens } from "./types"

/** Used when the comparison model has no price in opencode (e.g. a custom provider). */
export const TYPICAL_RATES: Rates = {
  label: "typical frontier-model",
  input: 3,
  output: 15,
  cacheRead: 0.3,
  cacheWrite: 3.75,
  typical: true,
}

export type PricedModel = { id: string; name?: string; cost?: { input?: number; output?: number; cache?: { read?: number; write?: number } } }
export type PricedProvider = { id: string; models?: Record<string, PricedModel> }

/** Rates for "provider/model" from opencode's provider list, or null if it has no price. */
export function ratesFromProviders(model: string, providers: PricedProvider[]): Rates | null {
  const slash = model.indexOf("/")
  if (slash <= 0) return null
  const provider = providers.find((p) => p.id === model.slice(0, slash))
  const id = model.slice(slash + 1)
  const m = provider?.models?.[id] ?? Object.values(provider?.models ?? {}).find((x) => x.id === id)
  const cost = m?.cost
  if (!cost || !(Number(cost.input) > 0 || Number(cost.output) > 0)) return null
  const input = Number(cost.input) || 0
  return {
    label: m?.name || id,
    model,
    input,
    output: Number(cost.output) || 0,
    cacheRead: Number(cost.cache?.read ?? input) || 0,
    cacheWrite: Number(cost.cache?.write ?? input) || 0,
    typical: false,
  }
}

export function readTokens(raw: unknown): Tokens | undefined {
  const t = raw as { input?: number; output?: number; reasoning?: number; cache?: { read?: number; write?: number } } | undefined
  if (!t || typeof t !== "object") return undefined
  const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0)
  return { input: n(t.input), output: n(t.output), reasoning: n(t.reasoning), cacheRead: n(t.cache?.read), cacheWrite: n(t.cache?.write) }
}

export function priceTokens(tokens: Tokens, rates: Rates): number {
  return (
    (tokens.input * rates.input +
      (tokens.output + tokens.reasoning) * rates.output +
      tokens.cacheRead * rates.cacheRead +
      tokens.cacheWrite * rates.cacheWrite) /
    1_000_000
  )
}

/** Adds wouldHaveCost and saved to a job. Open-cloud jobs saved nothing by definition. */
export function withSavings(job: Job, rates: Rates): Job {
  if (!job.tokens) return job
  const wouldHaveCost = priceTokens(job.tokens, rates)
  if (job.place === "open") return { ...job, wouldHaveCost, saved: 0 }
  return { ...job, wouldHaveCost, saved: wouldHaveCost - job.cost }
}

function monthOf(ms: number) {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}

/** Totals for jobs that already went through withSavings(). */
export function summarizeSavings(jobs: Job[], rates: Rates, now = Date.now()): Savings {
  const month = monthOf(now)
  const byPlace: Record<Place, number> = { device: 0, private: 0, open: 0 }
  let wouldHaveCost = 0
  let actual = 0
  let count = 0
  let allTime = 0
  for (const job of jobs) {
    if (job.place === "open" || job.saved === undefined) continue
    allTime += job.saved
    if (monthOf(job.created) !== month) continue
    wouldHaveCost += job.wouldHaveCost ?? 0
    actual += job.cost
    if (job.place) byPlace[job.place] += job.saved
    count += 1
  }
  return { month, wouldHaveCost, actual, saved: wouldHaveCost - actual, byPlace, jobs: count, allTime, rates }
}

/** "$1.23", "under 1¢", or "-$0.40" for a job that cost more than the open cloud would have. */
export function formatSaved(amount: number): string {
  if (amount < 0) return `−$${Math.abs(amount).toFixed(2)}`
  if (amount === 0) return "$0.00"
  if (amount < 0.01) return "under 1¢"
  return `$${amount.toFixed(2)}`
}

export function describeRates(rates: Rates): string {
  const price = (n: number) => `$${n % 1 === 0 ? n : n.toFixed(2)}`
  return `${price(rates.input)} in, ${price(rates.output)} out per million tokens`
}
