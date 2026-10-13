import { useState } from "react"
import { Wrench } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import AgentStrip from "./AgentStrip"
import EmulatorControls from "./EmulatorControls"
import SimControls from "./SimControls"
import { useMode } from "@/lib/live"

/** Everything that drives the data source lives here, never on the home page. */
export function DemoToolsButton() {
  const { mode } = useMode()
  const [open, setOpen] = useState(false)
  if (mode === "live") return null
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)} data-testid="demo-tools-button"><Wrench className="size-4" aria-hidden="true" />Demo tools</Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader><SheetTitle>Demo tools</SheetTitle>
            <SheetDescription>{mode === "emulator" ? "Control the labelled device emulator." : "Control the day-by-day replay of the TIHM study."}</SheetDescription></SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-6">
            {mode === "emulator" ? <EmulatorControls /> : <><SimControls /><AgentStrip /></>}
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
