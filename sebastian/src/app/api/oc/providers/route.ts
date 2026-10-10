import { oc, unwrap, withOc } from "@/lib/opencode/server"
import type { ProviderSummary } from "@/lib/opencode/types"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// opencode's response includes provider API keys and env details, so only
// names and model ids are passed to the browser.
export const GET = withOc(async () => {
  const data = unwrap(await oc().config.providers())
  const providers: ProviderSummary[] = (data?.providers ?? []).map((p) => ({
    id: p.id,
    name: p.name ?? p.id,
    models: Object.fromEntries(
      Object.entries(p.models ?? {}).map(([id, m]) => [id, { id: m.id ?? id, name: m.name ?? id }]),
    ),
  }))
  return Response.json({ providers, default: data?.default ?? {} })
})
