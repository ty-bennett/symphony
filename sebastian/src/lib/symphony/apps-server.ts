// Server-only: everything the Connected apps page shows.
import { OcError } from "@/lib/opencode/server"
import { APPS, appForWorkflow, type AppDef, type AppWorkflow } from "./apps"
import { cachedWorkflows, ensureRegistered, opencodeStatus, readN8nConfig } from "./n8n"
import type { Settings } from "./types"

export type AppsOverview = {
  connection: {
    state: "not-set-up" | "connected" | "error"
    source?: "env" | "saved"
    url?: string
    error?: string
    /** opencode's view of the n8n MCP server. */
    agent?: string
  }
  apps: (Omit<AppDef, "categories"> & { categories: string[]; ready: AppWorkflow[]; off: AppWorkflow[] })[]
  other: AppWorkflow[]
}

export async function appsOverview(settings: Settings, fresh = false): Promise<AppsOverview> {
  const labels = new Map(settings.keepPrivate.map((c) => [c.id, c.label]))
  const shape = (workflows: AppWorkflow[]) => {
    const usable = (w: AppWorkflow) => w.active && w.availableInMCP
    const byApp = new Map<string, AppWorkflow[]>()
    const other: AppWorkflow[] = []
    for (const w of workflows) {
      const app = appForWorkflow(w)
      if (app) byApp.set(app.id, [...(byApp.get(app.id) ?? []), w])
      else if (usable(w)) other.push(w)
    }
    return {
      apps: APPS.map((a) => ({
        ...a,
        categories: a.categories.map((id) => labels.get(id)).filter((l): l is string => Boolean(l)),
        ready: (byApp.get(a.id) ?? []).filter(usable),
        off: (byApp.get(a.id) ?? []).filter((w) => !usable(w)),
      })),
      other,
    }
  }

  const config = await readN8nConfig()
  if (!config) return { connection: { state: "not-set-up" }, ...shape([]) }
  try {
    const workflows = await cachedWorkflows(config, fresh ? 0 : 30_000)
    let agent: string | undefined
    try {
      agent = (await ensureRegistered(config)).status
    } catch (e) {
      agent = (await opencodeStatus())?.status ?? (e instanceof Error ? e.message : "unknown")
    }
    return { connection: { state: "connected", source: config.source, url: config.url, agent }, ...shape(workflows) }
  } catch (e) {
    const error = e instanceof OcError || e instanceof Error ? e.message : String(e)
    return { connection: { state: "error", source: config.source, url: config.url, error }, ...shape([]) }
  }
}
