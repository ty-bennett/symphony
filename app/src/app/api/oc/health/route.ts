import { oc, opencodeUrl, unwrap, withOc } from "@/lib/opencode/server"

export const dynamic = "force-dynamic"
export const runtime = "nodejs"

export const GET = withOc(async () => {
  const health = unwrap(await oc().global.health())
  return Response.json({ ...health, url: opencodeUrl() })
})
