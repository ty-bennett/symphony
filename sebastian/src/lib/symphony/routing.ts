// STAND-IN ROUTER. Symphony's real routing comes from the JEV model on the
// box. Until that is wired in, this keyword version makes the same decision
// from the same inputs, so the rest of the app can be built and tested.
// Replace routeTask() with a call to the JEV model; keep its return shape.
import { appsMentioned } from "./apps"
import type { Place, RouteDecision, Settings } from "./types"

const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/
const PHONE = /(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/

const WEB = ["search the web", "on the web", "online", "website", "internet", "google", "look up", "competitor", "latest news", "nearby", "other businesses", "reviews of"]
const BIG = ["analy", "compare", "report", "research", "forecast", "trend", "every ", "all of", "all our", "whole year", "last year", "this year", "quarter", "spreadsheet", "plan for"]

function includesAny(text: string, words: string[]) {
  return words.find((w) => text.includes(w))
}

/**
 * Keep-private categories a request touches. A request that mentions a
 * connected app (say Square) touches whatever that app brings into the job
 * (customers, money), because the agent will read it from there.
 */
export function privateHits(text: string, settings: Settings, connectedApps: string[] = []): string[] {
  const lower = text.toLowerCase()
  const fromApps = new Set(appsMentioned(text, connectedApps).flatMap((a) => a.categories))
  const hits: string[] = []
  for (const c of settings.keepPrivate) {
    if (!c.on) continue
    const contact = c.id === "customers" && (EMAIL.test(text) || PHONE.test(text))
    if (contact || fromApps.has(c.id) || includesAny(lower, c.keywords)) hits.push(c.label)
  }
  return hits
}

/** connectedApps: ids of apps that have actions set up (see apps.ts). */
export function routeTask(text: string, settings: Settings, connectedApps: string[] = []): RouteDecision {
  const lower = text.toLowerCase()
  const usable = settings.apps.enabled ? connectedApps : []
  const hits = privateHits(text, settings, usable)
  const apps = appsMentioned(text, usable).map((a) => a.name)
  // When the private part comes only from an app, say so ("reads ... from Square").
  const fromAppOnly = apps.length > 0 && privateHits(text, settings).length === 0
  const source = fromAppOnly ? ` from ${apps.join(" and ")}` : ""
  const web = includesAny(lower, WEB)
  const big = text.length > 600 || Boolean(includesAny(lower, BIG))
  const first = hits[0]?.toLowerCase()

  let place: Place
  let reason: string
  if (web) {
    place = "open"
    reason = "it needs to search the web"
  } else if (first && big) {
    place = "private"
    reason = fromAppOnly ? `it's a big job that reads ${first}${source}` : `it's a big job that includes ${first}`
  } else if (first) {
    place = "device"
    reason = fromAppOnly ? `it reads ${first}${source}` : `it includes ${first}`
  } else if (big) {
    place = "open"
    reason = "it's a big job with nothing private in it"
  } else {
    place = "device"
    reason = "it's a quick job"
  }

  // Respect what the owner has switched off: fall back to somewhere more private.
  const { private: priv, open } = settings.places
  if (place === "open" && (!open.enabled || (open.rule === "never" && hits.length > 0))) {
    place = priv.enabled ? "private" : "device"
    reason = web ? "the open cloud is off, so it won't search the web" : `the open cloud is off for ${hits.length ? "private info" : "jobs"}`
  }
  if (place === "private" && !priv.enabled) {
    place = "device"
    reason = "the private cloud is off"
  }

  const needsConsent = place === "open" && (open.rule === "ask-always" || hits.length > 0)
  return { place, reason, privateHits: hits, needsConsent, ...(apps.length ? { apps } : {}) }
}

/**
 * Replace email addresses and phone numbers with placeholders before text
 * leaves the device. Returns the redacted text and how to put values back.
 */
export function redactContactInfo(text: string): { text: string; redactions: Record<string, string> } {
  const redactions: Record<string, string> = {}
  let emails = 0
  let phones = 0
  const out = text
    .replace(new RegExp(EMAIL.source, "g"), (match) => {
      const key = `[email ${++emails}]`
      redactions[key] = match
      return key
    })
    .replace(new RegExp(PHONE.source, "g"), (match) => {
      const key = `[phone ${++phones}]`
      redactions[key] = match
      return key
    })
  return { text: out, redactions }
}

/** Put redacted values back, for display on the device only. */
export function restoreRedactions(text: string, redactions?: Record<string, string>): string {
  if (!redactions) return text
  let out = text
  for (const [key, value] of Object.entries(redactions)) out = out.split(key).join(value)
  return out
}
