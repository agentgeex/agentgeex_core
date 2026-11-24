/**
 * /plans/:planId/flow — plan flow chart route page
 * Reads planId from the URL and renders PlanFlow (the header title is dropped, the nav already has it; content in a white card).
 */
import { useParams } from "react-router-dom";
import { PlanFlow } from "./plan-flow";
import { useTranslation } from "react-i18next";
import { useDetailCrumb } from "@/lib/breadcrumb";

export default function PlanFlowRoute() {
  const { t } = useTranslation();
  const { planId = "" } = useParams();
  /* [2026-10-29] One more breadcrumb level: plans › flow chart (otherwise it only shows "Plans" and you cannot tell what you are viewing) */
  useDetailCrumb(t("miscShell.flow"));
  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* [2026-10-28 16:32 Feng] Drop the page header title/description (the nav has it); wrap the flow chart in a white card. */}
      <div
        className="min-h-0 flex-1 rounded-xl border border-strong bg-card p-3 shadow-sm"
        data-testid="content-frame"
      >
        <PlanFlow planId={planId} />
      </div>
    </div>
  );
}
