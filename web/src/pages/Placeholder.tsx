import { Construction } from "lucide-react"
import { Empty } from "@/components/States"

export default function Placeholder({ title }: { title: string }) {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <Empty title="Coming next">This view is planned for the next iteration. The Dashboard, Fall Monitor and Patient pages are ready.</Empty>
      <Construction className="sr-only" aria-hidden="true" />
    </div>
  )
}
