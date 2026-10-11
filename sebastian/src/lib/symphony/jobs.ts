// Server-only: a Symphony "job" is an opencode session plus the routing
// decision Symphony stored on its metadata.
import { OcError, oc, ocJson, parseModel, sendPrompt, unwrap } from "@/lib/opencode/server"
import type { RawPermission, SessionStatus } from "@/lib/opencode/types"
import { placeForProvider, PLACE_ORDER } from "./places"
import { redactContactInfo, routeTask } from "./routing"
import { ratesFromProviders, readTokens, summarizeSavings, TYPICAL_RATES, withSavings, type PricedProvider } from "./savings"
import type { Job, JobMeta, Place, Rates, Savings, Settings, Spending } from "./types"

type OcSession = {
  id: string
  title?: string
  parentID?: string
  cost?: number
  tokens?: unknown
  model?: { id: string; providerID: string }
  metadata?: Record<string, unknown>
  revert?: unknown
  time: { created: number; updated: number }
}

const RULES = ["edit", "bash", "webfetch"]

/** "Check with me first" becomes opencode permission rules on the session. */
export function permissionRules(checkFirst: boolean) {
  return RULES.map((permission) => ({ permission, pattern: "*", action: checkFirst ? ("ask" as const) : ("allow" as const) }))
}

export function readMeta(metadata: unknown): JobMeta | undefined {
  const m = (metadata as { symphony?: Partial<JobMeta> } | undefined)?.symphony
  if (!m || !PLACE_ORDER.includes(m.place as Place)) return undefined
  return {
    place: m.place as Place,
    reason: typeof m.reason === "string" ? m.reason : "",
    checkFirst: Boolean(m.checkFirst),
    ...(m.redactions && typeof m.redactions === "object" ? { redactions: m.redactions as Record<string, string> } : {}),
  }
}

function configuredModels(settings: Settings): Partial<Record<Place, string>> {
  return { device: settings.places.device.model, private: settings.places.private.model, open: settings.places.open.model }
}

export function toJob(session: OcSession, status: string | undefined, approvals: number, settings: Settings): Job {
  const meta = readMeta(session.metadata)
  const place = meta?.place ?? placeForProvider(session.model?.providerID, configuredModels(settings))
  return {
    id: session.id,
    title: session.title || "Untitled job",
    created: session.time.created,
    updated: session.time.updated,
    status: session.revert ? "undone" : approvals > 0 ? "needs-ok" : status === "busy" || status === "retry" ? "working" : "done",
    place,
    reason: meta?.reason,
    model: session.model ? `${session.model.providerID}/${session.model.id}` : undefined,
    checkFirst: meta?.checkFirst,
    cost: session.cost ?? 0,
    approvals,
    tokens: readTokens(session.tokens),
  }
}

// opencode's provider list is large and rarely changes; keep it a minute.
const priceCache = globalThis as unknown as { __symphonyPrices?: { at: number; providers: PricedProvider[]; model?: string } }

async function priceList() {
  const cached = priceCache.__symphonyPrices
  if (cached && Date.now() - cached.at < 60_000) return cached
  const [providers, config] = await Promise.all([
    oc().config.providers().then((r) => unwrap(r)?.providers ?? []),
    ocJson<{ model?: string }>("/config").catch(() => null),
  ])
  const fresh = { at: Date.now(), providers: providers as PricedProvider[], model: config?.model }
  priceCache.__symphonyPrices = fresh
  return fresh
}

/** The prices "saved" is measured against. Falls back to typical frontier prices. */
export async function compareRates(settings: Settings): Promise<Rates> {
  const choice = settings.savings.compareWith
  if (choice === "typical") return TYPICAL_RATES
  try {
    const prices = await priceList()
    const model = choice === "open" ? settings.places.open.model || prices.model || "" : choice
    return ratesFromProviders(model, prices.providers) ?? TYPICAL_RATES
  } catch {
    return TYPICAL_RATES
  }
}

export async function jobsWithSavings(settings: Settings): Promise<{ jobs: Job[]; savings: Savings }> {
  const [jobs, rates] = await Promise.all([listJobs(settings), compareRates(settings)])
  const priced = jobs.map((j) => withSavings(j, rates))
  return { jobs: priced, savings: summarizeSavings(priced, rates) }
}

async function snapshot() {
  const [sessions, status, pending] = await Promise.all([
    oc().session.list().then((r) => (unwrap(r) ?? []) as OcSession[]),
    ocJson<Record<string, SessionStatus>>("/session/status").catch(() => ({}) as Record<string, SessionStatus>),
    ocJson<RawPermission[]>("/permission").catch(() => [] as RawPermission[]),
  ])
  const approvals = new Map<string, number>()
  for (const p of pending ?? []) approvals.set(p.sessionID, (approvals.get(p.sessionID) ?? 0) + 1)
  return { sessions, status: status ?? {}, approvals }
}

export async function listJobs(settings: Settings): Promise<Job[]> {
  const { sessions, status, approvals } = await snapshot()
  return sessions
    .filter((s) => !s.parentID)
    .map((s) => toJob(s, status[s.id]?.type, approvals.get(s.id) ?? 0, settings))
    .sort((a, b) => b.updated - a.updated)
}

export async function getJob(id: string, settings: Settings): Promise<{ job: Job; meta?: JobMeta }> {
  const [session, { status, approvals }] = await Promise.all([
    oc().session.get({ sessionID: id }).then((r) => unwrap(r) as OcSession),
    snapshot(),
  ])
  return { job: toJob(session, status[id]?.type, approvals.get(id) ?? 0, settings), meta: readMeta(session.metadata) }
}

export function monthKey(ms: number) {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}

export function spending(jobs: Job[], settings: Settings, now = Date.now()): Spending {
  const month = monthKey(now)
  const byPlace: Record<Place, number> = { device: 0, private: 0, open: 0 }
  for (const job of jobs) {
    if (monthKey(job.created) !== month || !job.place) continue
    byPlace[job.place] += job.cost
  }
  const total = byPlace.device + byPlace.private + byPlace.open
  return { month, total, byPlace, limit: settings.spending.limit, atLimit: settings.spending.atLimit }
}

export type NewJob = {
  text: string
  checkFirst?: boolean
  /** Run somewhere other than where Symphony routed it. */
  place?: Place
  /** Owner agreed to send private info to the open cloud. */
  consent?: boolean
  /** Remove emails and phone numbers before the text leaves the device. */
  redact?: boolean
}

export class ConsentRequired extends OcError {
  constructor(readonly decision: ReturnType<typeof routeTask>) {
    super("This job would send private information to the open cloud. Choose how to handle it.", 409)
  }
}

export async function createJob(input: NewJob, settings: Settings): Promise<{ jobId: string; place: Place; reason: string }> {
  const text = input.text.trim()
  if (!text) throw new OcError("Describe the job first.", 400)
  const decision = routeTask(text, settings)
  let place: Place = decision.place
  let reason = decision.reason

  if (input.place && input.place !== decision.place) {
    if (!PLACE_ORDER.includes(input.place)) throw new OcError("Unknown place.", 400)
    if (input.place === "private" && !settings.places.private.enabled) throw new OcError("The private cloud is switched off.", 400)
    if (input.place === "open" && !settings.places.open.enabled) throw new OcError("The open cloud is switched off.", 400)
    place = input.place
    reason = PLACE_ORDER.indexOf(place) < PLACE_ORDER.indexOf(decision.place) ? "you chose to keep it more private" : "you chose this place"
  }

  if (place === "open") {
    const touchesPrivate = decision.privateHits.length > 0
    if (touchesPrivate && settings.places.open.rule === "never") {
      throw new OcError("Your settings never send private information to the open cloud.", 400)
    }
    if ((touchesPrivate || settings.places.open.rule === "ask-always") && !input.consent) throw new ConsentRequired(decision)
  }

  if (place !== "device") {
    const jobs = await listJobs(settings)
    const spent = spending(jobs, settings)
    if (spent.limit > 0 && spent.total >= spent.limit) {
      if (settings.spending.atLimit === "pause") {
        throw new OcError(
          `You've reached this month's $${spent.limit} limit. Raise it in Privacy and spending, or keep this job on your Symphony.`,
          402,
        )
      }
      place = "device"
      reason = "this month's spending limit is reached"
    }
  }

  const checkFirst = input.checkFirst ?? settings.checkFirst
  const model = settings.places[place].model || undefined
  const parsed = parseModel(model)
  const { text: sendText, redactions } =
    input.redact && place === "open" ? redactContactInfo(text) : { text, redactions: {} as Record<string, string> }
  const meta: JobMeta = { place, reason, checkFirst, ...(Object.keys(redactions).length ? { redactions } : {}) }

  // An explicit title keeps opencode from generating one, which would send
  // the request to its small model.
  const title = text.split("\n")[0].slice(0, 80)
  const session = unwrap(
    await oc().session.create({
      title,
      ...(parsed ? { model: { id: parsed.modelID, providerID: parsed.providerID } } : {}),
      permission: permissionRules(checkFirst),
      metadata: { symphony: meta },
    }),
  )
  await sendPrompt(session.id, { text: sendText, model })
  return { jobId: session.id, place, reason }
}

async function firstUserMessage(id: string): Promise<string> {
  const messages = unwrap(await oc().session.messages({ sessionID: id })) ?? []
  const first = messages.find((m) => m.info.role === "user")
  if (!first) throw new OcError("This job has nothing to undo yet.", 400)
  return first.info.id
}

/** Undo everything the job changed by reverting to before its first message. */
export async function undoJob(id: string) {
  const messageID = await firstUserMessage(id)
  return ocJson(`/session/${encodeURIComponent(id)}/revert`, { method: "POST", body: JSON.stringify({ messageID }) })
}

export async function redoJob(id: string) {
  return ocJson(`/session/${encodeURIComponent(id)}/unrevert`, { method: "POST" })
}
