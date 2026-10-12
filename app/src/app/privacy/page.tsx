import type { Metadata } from "next"
import { PrivacyView } from "@/components/symphony/PrivacyView"

export const metadata: Metadata = { title: "Privacy and spending" }

export default function PrivacyPage() {
  return <PrivacyView />
}
