import Link from "next/link"
import { EmptyState } from "@/components/ui/EmptyState"

export default function TodayPage() {
  return (
    <EmptyState
      title="Today isn't built yet"
      action={
        <Link href="/coding-agent">Open the coding agent</Link>
      }
    >
      <p>This screen is designed on the Symphony canvas. The coding agent is the first part of the app that works end to end.</p>
    </EmptyState>
  )
}
