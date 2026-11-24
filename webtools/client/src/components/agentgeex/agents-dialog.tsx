/**
 * components/agentgeex/agents-dialog.tsx — "agents of a host" → put the **agent page** straight into a dialog
 * ────────────────────────────────────────────────────────────────
 * [2026-11-05 23:39 Feng's words]
 *   "you got it wrong, you opened the agent shown inside your popup... the agent page itself,
 *     put it inside this popup and carry the query condition over, that is the most perfect solution."
 *
 * My first two versions were wrong in the same place:
 *   1) v1: click a number → **navigate** to /agents (cannot come back)
 *   2) v2: switched to a dialog, but inside was **a compact list I drew myself** — a second UI,
 *      without filters/sorting/grouping/view switching, not the same thing as the real agent page
 * Correct solution: `<AgentsBoard initialHost={hostId} embedded />` — the page itself moved into the dialog,
 *   the query condition (host_id) passed in as a prop. **Same component, different container.**
 *
 * Three side effects of `embedded` (all handled inside AgentsBoard, not patched here):
 *   · does not read/write the local filter archive (filters in a dialog are a temporary view, must not pollute /agents)
 *   · clicking a card does not navigate, instead layers an entity detail dialog (otherwise navigating loses the
 *     system status page)
 *   · removes the duplicated "box in a box" border
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Server } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AgentsBoard } from "@/pages/agentgeex/agents";

export function AgentsDialog({
  hostId,
  count,
  open,
  onOpenChange,
}: {
  hostId: string;
  /** The number on the host card, used for the top reconciliation (same source as the list, theoretically always equal) */
  count?: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        /* [2026-11-05 23:48 Feng] "the top-right of the popup lacks a close icon,
              use a single cross icon uniformly, other windows in the system use it too."
              → use DialogContent's built-in top-right × (the site-wide default one),
                and drop the "close" button I added at the bottom (two close entries = noise, the 11-03 lesson). */
        className="flex h-[92vh] w-[min(97vw,1480px)] max-w-none flex-col gap-0 overflow-hidden p-0"
        data-testid="agents-dialog"
        data-host={hostId}
        aria-describedby={undefined}
      >
        <DialogHeader className="shrink-0 border-b px-4 py-2.5 pr-12">
          <DialogTitle className="flex flex-wrap items-center gap-2 text-sm">
            <Server className="h-4 w-4 text-muted-foreground" />
            <span className="font-mono">{hostId}</span>
            <span className="text-[11px] font-normal text-muted-foreground" data-testid="agents-dialog-scope">
              {t("miscShell.agentsDialog.agentsOf")}
              {typeof count === "number" ? t("miscShell.agentsDialog.countSuffix", { count }) : ""}
            </span>
          </DialogTitle>
        </DialogHeader>

        {/* The page body: scroll this, not the whole page */}
        <div className="min-h-0 flex-1 overflow-y-auto" data-testid="agents-dialog-body">
          <AgentsBoard initialHost={hostId} embedded />
        </div>

      </DialogContent>
    </Dialog>
  );
}
