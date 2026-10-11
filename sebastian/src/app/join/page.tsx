import type { Metadata } from "next"
import { JoinView } from "@/components/auth/JoinView"

export const metadata: Metadata = { title: "Join the team" }

export default function Page() {
  return <JoinView />
}
