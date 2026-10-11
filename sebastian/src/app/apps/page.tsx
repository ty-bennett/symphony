import type { Metadata } from "next"
import { AppsView } from "@/components/symphony/AppsView"

export const metadata: Metadata = { title: "Connected apps" }

export default function AppsPage() {
  return <AppsView />
}
