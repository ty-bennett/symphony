import type { Metadata } from "next"
import { WorkList } from "@/components/symphony/WorkList"

export const metadata: Metadata = { title: "Work" }

export default function WorkPage() {
  return <WorkList />
}
