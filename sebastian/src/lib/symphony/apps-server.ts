// Server-only: everything the Connected apps page shows.
import { OcError } from "@/lib/opencode/server"
import { cachedTools, ensureAppRegistered, readAppServers } from "./app-servers"
import { APPS, appForWorkflow, isReadTool, type AppDef, type AppWorkflow } from "./apps"
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
  apps: (Omit<AppDef, "categories"> & {
    categories: string[]
    ready: AppWorkflow[]
    off: AppWorkflow[]
    /** The app's own n8n workflow (MCP Server Trigger with the app's tools), when connected. */
    server?: {
      state: "connected" | "error"
      url: string
      tools: { name: string; description: string; reads: boolean }[]
      error?: string
      /** opencode's view of it. */
      agent?: string
    }
  })[]
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

  const withServers = async (o: Omit<AppsOverview, "connection"> & { connection: AppsOverview["connection"] }): Promise<AppsOverview> => {
    const servers = await readAppServers()
    const apps = await Promise.all(
      o.apps.map(async (a) => {
        const server = servers[a.id]
        if (!server) return a
        try {
          const tools = await cachedTools(a.id, server, fresh ? 0 : 60_000)
          const agent = await ensureAppRegistered(a.id, server)
            .then((r) => r.status)
            .catch((e) => (e instanceof Error ? e.message : "unknown"))
          return {
            ...a,
            server: {
              state: "connected" as const,
              url: server.url,
              agent,
              tools: tools.map((t) => ({ name: t.name, description: t.description, reads: isReadTool(t.name) })),
            },
          }
        } catch (e) {
          return { ...a, server: { state: "error" as const, url: server.url, tools: [], error: e instanceof Error ? e.message : String(e) } }
        }
      }),
    )
    return { ...o, apps }
  }

  const config = await readN8nConfig()
  if (!config) return withServers({ connection: { state: "not-set-up" }, ...shape([]) })
  try {
    const workflows = await cachedWorkflows(config, fresh ? 0 : 30_000)
    let agent: string | undefined
    try {
      agent = (await ensureRegistered(config)).status
    } catch (e) {
      agent = (await opencodeStatus())?.status ?? (e instanceof Error ? e.message : "unknown")
    }
    return withServers({ connection: { state: "connected", source: config.source, url: config.url, agent }, ...shape(workflows) })
  } catch (e) {
    const error = e instanceof OcError || e instanceof Error ? e.message : String(e)
    return withServers({ connection: { state: "error", source: config.source, url: config.url, error }, ...shape([]) })
  }
}
