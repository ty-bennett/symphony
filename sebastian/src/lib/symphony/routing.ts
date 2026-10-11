// STAND-IN ROUTER. Symphony's real routing comes from the JEV model on the
// box (Soloist or Orchestra). Until that is wired in, this keyword version makes the same decision
// from the same inputs, so the rest of the app can be built and tested.
// Replace routeTask() with a call to the JEV model; keep its return shape.
import type { Place, RouteDecision, Settings } from "./types"

const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/
const PHONE = /(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/

const WEB = ["search the web", "on the web", "online", "website", "internet", "google", "look up", "competitor", "latest news", "nearby", "other businesses", "reviews of"]
const BIG = ["analy", "compare", "report", "research", "forecast", "trend", "every ", "all of", "all our", "whole year", "last year", "this year", "quarter", "spreadsheet", "plan for"]

function includesAny(text: string, words: string[]) {
  return words.find((w) => text.includes(w))
}

/** Keep-private categories a request touches. */
export function privateHits(text: string, settings: Settings): string[] {
  const lower = text.toLowerCase()
  const hits: string[] = []
  for (const c of settings.keepPrivate) {
    if (!c.on) continue
    const contact = c.id === "customers" && (EMAIL.test(text) || PHONE.test(text))
    if (contact || includesAny(lower, c.keywords)) hits.push(c.label)
  }
  return hits
}

export function routeTask(text: string, settings: Settings): RouteDecision {
  const lower = text.toLowerCase()
  const hits = privateHits(text, settings)
  const web = includesAny(lower, WEB)
  const big = text.length > 600 || Boolean(includesAny(lower, BIG))
  const first = hits[0]?.toLowerCase()

  let place: Place
  let reason: string
  if (web) {
    place = "private"
    reason = "it needs the web"
  } else if (big) {
    place = "private"
    reason = first ? `it's a big job that includes ${first}` : "it's a big job"
  } else {
    place = "device"
    reason = first ? `it includes ${first}` : "it's a quick job"
  }

  // Orchestra switched off: everything stays on the box.
  if (place === "private" && !settings.places.private.enabled) {
    place = "device"
    reason = "Orchestra is off"
  }
  return { place, reason, privateHits: hits }
}
