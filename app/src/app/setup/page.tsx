import type { Metadata } from "next"
import { SetupView } from "@/components/auth/SetupView"

export const metadata: Metadata = { title: "Set up" }

export default function Page() {
  return <SetupView />
}
