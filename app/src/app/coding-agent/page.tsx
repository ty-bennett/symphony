import type { Metadata } from "next"
import { CodingAgentRoute } from "./CodingAgentRoute"

export const metadata: Metadata = { title: "Coding agent" }

type Props = { searchParams: Promise<{ session?: string | string[] }> }

export default async function CodingAgentPage({ searchParams }: Props) {
  const { session } = await searchParams
  return <CodingAgentRoute initialSessionId={typeof session === "string" && session ? session : null} />
}
