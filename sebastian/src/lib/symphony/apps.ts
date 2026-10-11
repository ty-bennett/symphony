// Connected apps: the business apps Symphony's agent can use through n8n.
// Shared by server and browser; no I/O here.
//
// How it fits together: n8n runs next to Symphony and holds the app logins.
// Each action ("See today's Square sales") is an n8n workflow marked
// "Available in MCP". n8n's built-in MCP server is registered with opencode,
// so the agent can find and run those workflows. Symphony only lets jobs use
// the read-only finding tools plus "run a workflow" (which asks first); n8n's
// other MCP tools (building, deleting, publishing workflows, credentials) are
// hidden from every session.

/** Name the n8n MCP server is registered under in opencode. Tools become `n8n_<tool>`. */
export const N8N_SERVER = "n8n"

export type AppDef = {
  id: string
  name: string
  /** One line on what it is for, in the owner's words. */
  about: string
  /** Examples of what the agent can do once actions are set up. */
  examples: string[]
  /** Words that mean a request is about this app. Lower case. */
  keywords: string[]
  /** Keep-private categories (settings ids) whose data this app brings into a job. */
  categories: string[]
  /** How n8n talks to it. */
  via: "built-in" | "http"
  /** n8n node types that mean a workflow uses this app. */
  nodeTypes?: string[]
  /** Ready-made n8n workflow (MCP Server Trigger with this app's tools) in public/n8n/. */
  workflow?: string
}

export const APPS: AppDef[] = [
  {
    id: "square",
    name: "Square",
    about: "Sales, payments and customers from your register",
    examples: ["What did we sell today?", "Send Jane a payment link for the cake"],
    keywords: ["square", "point of sale", "register sales"],
    categories: ["customers", "money"],
    via: "http",
    workflow: "/n8n/square-for-symphony.json",
  },
  {
    id: "google-calendar",
    name: "Google Calendar",
    about: "Appointments, pickups and bookings",
    examples: ["What pickups are booked for Saturday?", "Book a tasting on Friday at 3"],
    keywords: ["google calendar", "calendar", "appointment", "booking"],
    categories: ["customers"],
    via: "built-in",
    nodeTypes: ["n8n-nodes-base.googleCalendar", "n8n-nodes-base.googleCalendarTrigger"],
  },
  {
    id: "gmail",
    name: "Gmail",
    about: "Customer emails and order requests",
    examples: ["Summarize this week's catering inquiries", "Reply to Jane and confirm Saturday"],
    keywords: ["gmail", "inbox", "email"],
    categories: ["customers"],
    via: "built-in",
    workflow: "/n8n/gmail-for-symphony.json",
    nodeTypes: ["n8n-nodes-base.gmail", "n8n-nodes-base.gmailTrigger"],
  },
  {
    id: "quickbooks",
    name: "QuickBooks",
    about: "Invoices, bills and bookkeeping",
    examples: ["Which invoices are overdue?", "Invoice the Smiths for the wedding cake"],
    keywords: ["quickbooks", "invoice", "bookkeeping"],
    categories: ["money", "customers"],
    via: "built-in",
    workflow: "/n8n/quickbooks-for-symphony.json",
    nodeTypes: ["n8n-nodes-base.quickbooks"],
  },
  {
    id: "shopify",
    name: "Shopify",
    about: "Online orders and products",
    examples: ["List today's online orders", "Which products are low on stock?"],
    keywords: ["shopify", "online store", "online order"],
    categories: ["customers", "money"],
    via: "built-in",
    nodeTypes: ["n8n-nodes-base.shopify", "n8n-nodes-base.shopifyTrigger"],
  },
  {
    id: "google-sheets",
    name: "Google Sheets",
    about: "Schedules, price lists and order sheets",
    examples: ["Add today's specials to the menu sheet", "Read next week's availability sheet"],
    keywords: ["google sheet", "spreadsheet", "sheet"],
    categories: [],
    via: "built-in",
    nodeTypes: ["n8n-nodes-base.googleSheets", "n8n-nodes-base.googleSheetsTrigger"],
  },
  {
    id: "mailchimp",
    name: "Mailchimp",
    about: "Newsletters and your mailing list",
    examples: ["How many people joined the list this month?", "Send the October newsletter"],
    keywords: ["mailchimp", "newsletter", "mailing list"],
    categories: ["customers"],
    via: "built-in",
    workflow: "/n8n/mailchimp-for-symphony.json",
    nodeTypes: ["n8n-nodes-base.mailchimp", "n8n-nodes-base.mailchimpTrigger"],
  },
]

/** A workflow n8n reports through its MCP server's search_workflows tool. */
export type AppWorkflow = {
  id: string
  name: string
  description?: string | null
  active: boolean
  availableInMCP: boolean
  tags: string[]
  /** Node types in the workflow, when n8n reports them (n8n 2.8 does; later versions don't). */
  nodes?: string[]
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim()

/** Which app a workflow belongs to: a tag naming the app, then the app's n8n node, then its name and description. */
export function appForWorkflow(w: Pick<AppWorkflow, "name" | "description" | "tags" | "nodes">): AppDef | null {
  const tags = w.tags.map(norm)
  const byTag = APPS.find((a) => tags.includes(norm(a.name)) || tags.includes(norm(a.id)))
  if (byTag) return byTag
  const byNode = APPS.find((a) => a.nodeTypes?.some((t) => w.nodes?.includes(t)))
  if (byNode) return byNode
  const text = ` ${norm(`${w.name} ${w.description ?? ""}`)} `
  return APPS.find((a) => a.keywords.some((k) => text.includes(` ${norm(k)} `))) ?? null
}

/** Apps a request mentions, among the ones that have actions set up. */
export function appsMentioned(text: string, connected: string[]): AppDef[] {
  const t = ` ${norm(text)} `
  return APPS.filter((a) => connected.includes(a.id) && a.keywords.some((k) => t.includes(` ${norm(k)} `)))
}

type Rule = { permission: string; pattern: string; action: "allow" | "ask" | "deny" }

/**
 * opencode permission rules for n8n's tools on one session. The last matching
 * rule wins, and a tool whose rule is deny "*" is hidden from the model.
 */
export function appPermissionRules({ enabled, ask }: { enabled: boolean; ask: boolean }): Rule[] {
  const rules: Rule[] = [{ permission: `${N8N_SERVER}_*`, pattern: "*", action: "deny" }]
  if (!enabled) return rules
  for (const tool of ["search_workflows", "get_workflow_details", "get_execution"]) {
    rules.push({ permission: `${N8N_SERVER}_${tool}`, pattern: "*", action: "allow" })
  }
  rules.push({ permission: `${N8N_SERVER}_execute_workflow`, pattern: "*", action: ask ? "ask" : "allow" })
  return rules
}

/** Tools whose names start with these only read, so they run without asking. Everything else asks. */
export const READ_PREFIXES = ["search", "read", "get", "list", "find", "look_up", "check", "count"]

export function isReadTool(tool: string): boolean {
  return READ_PREFIXES.some((p) => tool === p || tool.startsWith(`${p}_`))
}

/**
 * Rules for apps connected through their own n8n workflow (registered in
 * opencode under the app id, so tools are `<app>_<tool>`): reading tools
 * run, everything else asks first (or runs, if asking is switched off).
 */
export function appServerRules(appIds: string[], { enabled, ask }: { enabled: boolean; ask: boolean }): Rule[] {
  const rules: Rule[] = []
  for (const id of appIds) {
    rules.push({ permission: `${id}_*`, pattern: "*", action: enabled ? (ask ? "ask" : "allow") : "deny" })
    if (!enabled) continue
    for (const p of READ_PREFIXES) {
      rules.push({ permission: `${id}_${p}`, pattern: "*", action: "allow" })
      rules.push({ permission: `${id}_${p}_*`, pattern: "*", action: "allow" })
    }
  }
  return rules
}

/** Which app an opencode tool name belongs to, and the tool's own name. */
export function appOfTool(tool: string): { app: AppDef; name: string } | null {
  const app = APPS.find((a) => tool.startsWith(`${a.id}_`))
  return app ? { app, name: tool.slice(app.id.length + 1) } : null
}

/** Readable names for n8n tool calls in steps and approval cards. */
export function describeAppTool(tool: string, workflowName?: string): string | null {
  const own = appOfTool(tool)
  if (own) {
    const words = own.name.replace(/_/g, " ")
    return `${own.app.name}: ${words.charAt(0).toUpperCase()}${words.slice(1)}`
  }
  if (!tool.startsWith(`${N8N_SERVER}_`)) return null
  const name = tool.slice(N8N_SERVER.length + 1)
  const quoted = workflowName ? `“${workflowName}”` : "an app action"
  switch (name) {
    case "execute_workflow":
      return `Run ${quoted}`
    case "get_execution":
      return workflowName ? `Get the result of ${quoted}` : "Get an app action's result"
    case "get_workflow_details":
      return workflowName ? `Check what ${quoted} needs` : "Check what an app action needs"
    case "search_workflows":
      return "Look for app actions"
    default:
      return `Use n8n (${name.replace(/_/g, " ")})`
  }
}
