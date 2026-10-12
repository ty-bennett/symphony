import type { Metadata } from "next"
import { TodayView } from "@/components/symphony/TodayView"

export const metadata: Metadata = { title: "Today" }

export default function TodayPage() {
  return <TodayView />
}
