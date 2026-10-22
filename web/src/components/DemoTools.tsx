import { useState } from "react"
import { Wrench } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import AgentStrip from "./AgentStrip"
import InjectReading from "./InjectReading"
import SimControls from "./SimControls"

/** Everything that drives the data source lives here, never on the home page. */
export function DemoToolsButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)} data-testid="demo-tools-button"><Wrench className="size-4" aria-hidden="true" />Demo tools</Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader><SheetTitle>Demo tools</SheetTitle>
            <SheetDescription>Control the day-by-day replay of the TIHM study, inject a reading, and test an agent crash.</SheetDescription></SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-6"><SimControls /><InjectReading /><AgentStrip /></div>
        </SheetContent>
      </Sheet>
    </>
  )
}
