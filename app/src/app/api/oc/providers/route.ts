import { oc, unwrap, ownerOnly } from "@/lib/opencode/server"
import type { ProviderSummary } from "@/lib/opencode/types"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

// opencode's response includes provider API keys and env details, so only
// names, model ids and per-million-token prices are passed to the browser.
export const GET = ownerOnly(async () => {
  const data = unwrap(await oc().config.providers())
  const providers: ProviderSummary[] = (data?.providers ?? []).map((p) => ({
    id: p.id,
    name: p.name ?? p.id,
    models: Object.fromEntries(
      Object.entries(p.models ?? {}).map(([id, m]) => [
        id,
        {
          id: m.id ?? id,
          name: m.name ?? id,
          ...(m.cost && (m.cost.input > 0 || m.cost.output > 0) ? { cost: { input: m.cost.input, output: m.cost.output } } : {}),
        },
      ]),
    ),
  }))
  return Response.json({ providers, default: data?.default ?? {} })
})
