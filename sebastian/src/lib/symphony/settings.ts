import type { PrivacyCategory, Settings } from "./types"

export const DEFAULT_CATEGORIES: PrivacyCategory[] = [
  {
    id: "customers",
    label: "Customer contact info",
    keywords: ["customer", "client", "guest", "catering inquir", "email address", "phone number", "home address", "order history"],
    on: true,
  },
  {
    id: "staff",
    label: "Staff and payroll",
    keywords: ["staff", "employee", "payroll", "salary", "salaries", "wage", "timesheet", "shift", "pay rate"],
    on: true,
  },
  {
    id: "money",
    label: "Bank and card details",
    keywords: ["bank", "card number", "credit card", "account number", "routing number", "deposit", "social security", "ssn", "tax id", "ein"],
    on: true,
  },
  { id: "contracts", label: "Contracts", keywords: ["contract", "agreement", "lease"], on: true },
  { id: "recipes", label: "Recipes", keywords: ["recipe"], on: false },
  { id: "suppliers", label: "Supplier prices", keywords: ["supplier", "vendor", "wholesale"], on: false },
]

export const DEFAULT_SETTINGS: Settings = {
  businessType: "Bakery and café",
  checkFirst: true,
  keepPrivate: DEFAULT_CATEGORIES,
  places: {
    device: { model: "" },
    private: { enabled: true, model: "" },
    open: { enabled: true, model: "", rule: "ask-if-private" },
  },
  spending: { limit: 40, atLimit: "pause" },
  savings: { compareWith: "open" },
  apps: { enabled: true, askFirst: true },
}

/** Fill in anything missing from a stored or submitted settings object. */
export function normalizeSettings(input: unknown): Settings {
  const s = (input && typeof input === "object" ? input : {}) as Partial<Settings>
  const d = DEFAULT_SETTINGS
  const places = (s.places ?? {}) as Partial<Settings["places"]>
  const str = (v: unknown, fallback: string) => (typeof v === "string" ? v : fallback)
  const bool = (v: unknown, fallback: boolean) => (typeof v === "boolean" ? v : fallback)
  const categories = Array.isArray(s.keepPrivate)
    ? s.keepPrivate
        .filter((c): c is PrivacyCategory => Boolean(c && typeof c.id === "string" && typeof c.label === "string"))
        .map((c) => ({
          id: c.id,
          label: c.label.slice(0, 60),
          keywords: Array.isArray(c.keywords) ? c.keywords.filter((k) => typeof k === "string").map((k) => k.toLowerCase()) : [],
          on: bool(c.on, true),
          ...(c.custom ? { custom: true } : {}),
        }))
    : d.keepPrivate
  const rule = places.open?.rule
  const limit = Number(s.spending?.limit)
  return {
    businessType: str(s.businessType, d.businessType),
    checkFirst: bool(s.checkFirst, d.checkFirst),
    keepPrivate: categories,
    places: {
      device: { model: str(places.device?.model, "") },
      private: { enabled: bool(places.private?.enabled, true), model: str(places.private?.model, "") },
      open: {
        enabled: bool(places.open?.enabled, true),
        model: str(places.open?.model, ""),
        rule: rule === "ask-always" || rule === "never" ? rule : "ask-if-private",
      },
    },
    spending: {
      limit: Number.isFinite(limit) && limit >= 0 ? Math.round(limit * 100) / 100 : d.spending.limit,
      atLimit: s.spending?.atLimit === "device-only" ? "device-only" : "pause",
    },
    apps: {
      enabled: bool(s.apps?.enabled, true),
      askFirst: bool(s.apps?.askFirst, true),
    },
    savings: {
      compareWith:
        typeof s.savings?.compareWith === "string" && s.savings.compareWith.trim() ? s.savings.compareWith.trim().slice(0, 200) : "open",
    },
  }
}

/** A custom keep-private item: matched on the words the owner typed. */
export function customCategory(label: string): PrivacyCategory {
  const clean = label.trim().slice(0, 60)
  const id = `custom-${clean.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || Date.now()}`
  // Match the singular too ("wedding orders" also catches "a wedding order").
  const lower = clean.toLowerCase()
  const stem = lower.length > 3 && lower.endsWith("s") && !lower.endsWith("ss") ? lower.slice(0, -1) : lower
  return { id, label: clean, keywords: [stem], on: true, custom: true }
}
