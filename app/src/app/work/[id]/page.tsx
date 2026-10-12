import type { Metadata } from "next"
import { JobView } from "@/components/symphony/JobView"

export const metadata: Metadata = { title: "Job" }

type Props = { params: Promise<{ id: string }> }

export default async function JobPage({ params }: Props) {
  const { id } = await params
  return <JobView key={id} id={id} />
}
