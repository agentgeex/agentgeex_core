/**
 * Agent management · quick commands (/quick-cmds)
 *
 * Basis: "Quick Command Data Spec v2.1" + Feng 2026-10-14 "agx_quick_cmd table + hook it into the agent management menu".
 *
 * Features:
 *   - list: title / category / owner agent / target task / param count / status (driven by table agx_quick_cmd)
 *   - filter: category
 *   - search: title / command id / owner / task / semantics
 *   - "New": schema-driven form (title/owner/task/category/instruction template + simple param schema editor)
 *   - row actions: run (through the generic "message send" window → QuickCmdSendDialog) / edit / archive
 *   - details: click a row → dialog with the full config (read-only)
 *
 * [2026-10-16 Feng] Default "grid (card)" view (defaultView="card") —
 *   the old table strip left too much whitespace between text and actions; the view toggle stays for one-click switch back.
 *
 * UI invents nothing: the list reuses components/agentgeex/data-table (pagination/filter/dual view),
 * the run dialog reuses components/agentgeex/quick-cmd-dialog, the form reuses ui/input and ui/dialog.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Plus, Pencil, Trash2, Play, Terminal, Save, Loader2, Info, UserCheck, Copy, ClipboardList, ListChecks } from "lucide-react";
import { DataTable, type ColumnDef } from "@/components/agentgeex/data-table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormRow } from "@/components/ui/form-field";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { QuickCmdSendDialog } from "@/components/agentgeex/quick-cmd-send-dialog";
import {
  fetchQuickCommands, createQuickCommand, updateQuickCommand, removeQuickCommand,
  listAgentOptions, agentOptionText, listProjects, getProjectName,
  sortByQuickProject, projectNameWithPin,
  type QuickCmd, type QuickCmdParam,
} from "@/providers/agentgeex";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { cardStateClass, useCardSelection } from "@/lib/card-selection";
/* [2026-11-14 Feng] The form is now a standalone dialog: "frequent phrases" in the compose window must be able
   to create one in place, sharing the same form with the management page (not a copy), otherwise the validation
   rules will drift apart sooner or later. */
import { QuickCmdForm, categoryLabel } from "@/components/agentgeex/quick-cmd-form-dialog";

/**
 * [2026-11-04 22:23 Feng agreed · remove the frontend "high-risk command" greying]
 *
 * There used to be a hard-coded table here { agx_agentfather→agent_manager, agx_projectmother→project_manager };
 * whenever ownerAgent matched, the current identity had to hold that role, otherwise the "Run" button was disabled.
 * It had three compounding flaws that ended up locking 7 commands so that **nobody could click them**:
 *
 *   ① The gate read an input that had already been deleted — userRoles came from the caller_agent_id of the list
 *      API, and after the "current operating identity" dropdown was removed on 2026-10-29, the list page stopped
 *      passing it → always [].
 *   ② It claimed "same criteria as the high-risk fence in the kernel's agx_quick_cmd_send",
 *      but that fence was deleted wholesale on 2026-10-25 (reason: it blocked the PM's legitimate path,
 *      and the L5 baseline is enough). The backend had opened up, the frontend kept adding restrictions.
 *   ③ Button disabled → dialog cannot open; the detail page has no run entry → those 7 cannot be executed in the UI at all.
 *
 * Now one criterion: **permissions are decided by the backend**.
 * The "sending agent" chosen in the run dialog is the real caller; the kernel judges by the L5 baseline and
 * returns 403 PERMISSION_DENIED if insufficient, and the dialog already has friendly copy (see quick-cmd-dialog.tsx).
 * The frontend does not pre-guess "who has permission" — guessing wrong costs a legitimate action being unclickable,
 * far worse than one extra 403.
 */
/** [2026-11-14 Feng] Identifiable marker for the two kinds of command.
 *  dispatch task = original behaviour (with params, creates an execution);
 *  text snippet = a stored frequently used phrase, only fills the input box, **creates no execution**. */
function SendModeBadge({ mode }: { mode?: "task" | "text" }) {
  const { t } = useTranslation();
  return mode === "text" ? (
    <Badge
      variant="outline"
      className="gap-1 border-info/30 bg-info/10 font-normal text-info"
      data-testid="quick-cmd-mode-text"
    >
      <ClipboardList className="h-3 w-3" />
      {t("quickCmdsPage.mode.text")}
    </Badge>
  ) : (
    <Badge
      variant="outline"
      className="gap-1 border-primary/30 bg-primary/10 font-normal text-primary"
      data-testid="quick-cmd-mode-task"
    >
      <ListChecks className="h-3 w-3" />
      {t("quickCmdsPage.mode.task")}
    </Badge>
  );
}

const CATEGORY_STYLE: Record<string, string> = {
  network: "bg-info/15 text-info border-info/30",
  system: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30",
  notice: "bg-warning/15 text-warning border-warning/30",
  agent: "bg-chart-1/15 text-chart-1 border-chart-1/30",
  host: "bg-success/15 text-success border-success/30",
  project: "bg-destructive/15 text-destructive border-destructive/30",
  dev: "bg-chart-2/15 text-chart-2 border-chart-2/30",
};

function CategoryBadge({ category }: { category: string }) {
  const { t } = useTranslation();
  return (
    <Badge variant="outline" className={`font-normal ${CATEGORY_STYLE[category] ?? "bg-muted text-muted-foreground"}`}>
      {categoryLabel(category, t)}
    </Badge>
  );
}

/** [2026-10-26 public/private] Marker: public = not bound to an agent, whoever clicks runs it (the caller itself). */
function VisibilityBadge({ isPublic }: { isPublic?: number }) {
  const { t } = useTranslation();
  return isPublic ? (
    <Badge
      variant="outline"
      className="border-info/40 bg-info/15 font-normal text-info"
      data-testid="quick-cmd-public-badge"
    >
      {t("quickCmdsPage.visibility.public")}
    </Badge>
  ) : (
    <Badge variant="outline" className="bg-muted font-normal text-muted-foreground">
      {t("quickCmdsPage.visibility.private")}
    </Badge>
  );
}

/**
 * Executor display (2026-10-27 Feng's criterion):
 *   is_public is not a permission boundary, it only says **who executes** —
 *     public → the clicker executes it (executor = the currently selected agent identity)
 *     private → dispatched to the agent target_agent_id for execution
 * So "executor" is more accurate than "owner" here. For public commands the backend ownerAgent is empty.
 */
const executorLabel = (cmd: QuickCmd, t: (k: string) => string): string =>
  cmd.isPublic ? t("quickCmdsPage.executor.self") : cmd.ownerAgent || "—";

function StatusBadge({ active }: { active: number }) {
  const { t } = useTranslation();
  return active ? (
    <Badge variant="outline" className="border-success/30 bg-success/15 font-normal text-success">
      {t("quickCmdsPage.status.active")}
    </Badge>
  ) : (
    <Badge variant="outline" className="bg-muted font-normal text-muted-foreground">
      {t("quickCmdsPage.status.inactive")}
    </Badge>
  );
}

/* ------------------------- form (new/edit) ------------------------- */

/* ------------------------------ page ------------------------------ */
export default function QuickCmdsPage() {
  const { t } = useTranslation();
  const [rows, setRows] = React.useState<QuickCmd[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  /* [2026-10-29 Feng] The "current operating identity" dropdown on the list page is gone: selecting it filtered
     nothing and had no real effect. The identity is now chosen in the "run dialog" (that is what really decides
     the caller).
     [2026-11-04 22:23] Removed the userRoles state along with it — it only served the frontend greying,
     which is gone, so keeping it means keeping a variable that "looks like it checks permissions but nobody reads". */

  // [query conditions 2026-10-14 Feng] data sources for the Agent / Project dropdown filters:
  //   Agents come from de-duplicating the commands' ownerAgent; projects from the ownerAgent → Agent.project_id map.
  const navigate = useNavigate();
  const { toast } = useToast();
  /* [2026-11-05 12:41 Feng] "It works, roll it out to all grid pages"
     —— shares @/lib/card-selection with the agent grid: the same hover, the same selected state,
        the same "still highlighted after going into details and back" behaviour. Two implementations
        drift apart sooner or later, so they are one source. */
  const cards = useCardSelection({ storageKey: "agentgeex.quickcmds.selected", testIdPrefix: "quick-cmd-card-" });
  /* [2026-11-07] Filter options must be complete — retired agents may still have historical commands to look up. */
  const agents = React.useMemo(() => listAgentOptions(), []);
  const projects = React.useMemo(() => listProjects(), []);

  // run / form dialogs (details moved to the standalone page /quick-cmds/:cmdId)
  const [runCmd, setRunCmd] = React.useState<QuickCmd | null>(null);
  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<QuickCmd | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetchQuickCommands(undefined);
      setRows(res.commands || []);
    } catch (e) {
      const x = e as { message?: string };
      setError(x.message || t("quickCmdsPage.error.load"));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { void load(); }, [load]);

  /** [2026-11-14 Feng] Text snippet: copies the text to the clipboard only, **sends nothing, no execution**. */
  const copyCmdText = React.useCallback(async (row: QuickCmd) => {
    const text = row.composedInstruction || "";
    if (!text) {
      toast({
        title: t("quickCmdsPage.copy.emptyTitle"),
        description: row.cmdId,
        variant: "destructive",
      });
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      toast({
        title: t("quickCmdsPage.copy.copied"),
        description: t("quickCmdsPage.copy.copiedDesc", {
          name: row.title || row.cmdId,
          n: text.length,
        }),
        variant: "success",
      });
    } catch {
      /* non-https / clipboard blocked → fall back to "select it so the user copies manually", never fail silently */
      window.prompt(t("quickCmdsPage.copy.failedPrompt"), text);
    }
  }, [toast]);

  async function handleArchive(cmd: QuickCmd) {
    if (!window.confirm(t("quickCmdsPage.archiveConfirm", { name: cmd.title }))) return;
    try {
      await removeQuickCommand(cmd.cmdId);
      await load();
    } catch (e) {
      const x = e as { message?: string };
      window.alert(x.message || t("quickCmdsPage.error.archive"));
    }
  }

  const columns: ColumnDef<QuickCmd>[] = [
    { key: "title", header: t("quickCmdsPage.column.title"), cardTitle: true },
    { key: "cmdId", header: t("quickCmdsPage.column.cmdId"), className: "font-mono text-xs", render: (r) => <span className="font-mono text-xs text-muted-foreground">{r.cmdId}</span> },
    { key: "category", header: t("quickCmdsPage.column.category"), sortable: true, render: (r) => <CategoryBadge category={r.category || "other"} /> },
    /* [2026-11-14 Feng] The two kinds must be distinguishable at a glance: dispatch task creates an execution, a text
   snippet only fills the input. */
    { key: "sendMode", header: t("quickCmdsPage.column.kind"), sortable: true, render: (r) => <SendModeBadge mode={r.sendMode} /> },
    { key: "isPublic", header: t("quickCmdsPage.column.visibility"), sortable: true, render: (r) => <VisibilityBadge isPublic={r.isPublic} /> },
    { key: "executor", header: t("quickCmdsPage.column.executor"), sortable: true, render: (r) => (
      <span className="text-xs" data-testid={`quick-cmd-executor-${r.cmdId}`}>
        {r.isPublic ? (
          <span className="inline-flex items-center gap-1 text-info">
            <UserCheck className="h-3 w-3" /> {t("quickCmdsPage.executor.self")}
          </span>
        ) : (
          <span className="font-mono">{r.ownerAgent || "—"}</span>
        )}
      </span>
    ) },
    { key: "targetTask", header: t("quickCmdsPage.column.targetTask"), render: (r) => <span className="font-mono text-xs text-muted-foreground">{r.targetTask}</span> },
    { key: "params", header: t("quickCmdsPage.column.params"), className: "w-[64px] text-right", render: (r) => <span className="tabular-nums">{r.params?.length ?? 0}</span> },
    { key: "activeStatus", header: t("quickCmdsPage.column.status"), render: (r) => <StatusBadge active={r.activeStatus ?? 1} /> },
  ];

  // [query conditions 2026-10-14 Feng] the Agent / Project dropdowns.
  //   agentId → projectId map (derived from Agents; empty when missing).
  const agentProject = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const a of agents) m.set(a.id, a.project_id || "");
    return m;
  }, [agents]);
  const projectOfCmd = React.useCallback(
    (r: QuickCmd) => agentProject.get(r.ownerAgent || "") || "",
    [agentProject]
  );
  /* [2026-11-07 Feng] Filter candidates = agents that appear in the data (not all 100) —
     choosing one of the 88 that would always be empty only lengthens the dropdown and returns nothing.
     But **never drop one just because it is offline**: labels are completed against the full agent table
     (including "· retired / · template"), so the one that "has commands but is retired" is still in the
     list and clearly marked as retired. */
  const agentOptions = React.useMemo(() => {
    const present = Array.from(new Set(rows.map((r) => r.ownerAgent).filter(Boolean))).sort();
    const byId = new Map(agents.map((a) => [a.id, a]));
    return present.map((id) => {
      const a = byId.get(id);
      return { label: a ? agentOptionText(a) : id, value: id };
    });
  }, [rows, agents]);
  const projectOptions = React.useMemo(
    /* [2026-11-17 15:07] Pinned projects first (★ marker), the rest by project id. */
    () => sortByQuickProject(
      Array.from(new Set(rows.map((r) => projectOfCmd(r)).filter(Boolean))).sort()
        .map((pid) => ({
          label: projectNameWithPin(pid, getProjectName(pid) || t("agentsPage.unregisteredProject")),
          value: pid,
        })),
      (x) => x.value,
    ),
    [rows, projectOfCmd]
  );

  // [2026-10-16 Feng] quick command card = same "large grid" look as the "task" page.
  //   Row actions in a shared function: table view (row end) and grid view (card bottom right), no two copies.
  const renderCmdActions = (row: QuickCmd) => (
    <div className="flex items-center gap-1">
      {/* details: standalone page (2026-10-26 Feng: a dialog cannot show everything, and the link must be shareable) */}
      <Button
        size="sm"
        variant="view"
        className="h-7 px-2"
        onClick={(e) => { e.stopPropagation(); navigate(`/quick-cmds/${encodeURIComponent(row.cmdId)}`); }}
        data-testid={`quick-cmd-detail-${row.cmdId}`}
      >
        <Info className="h-3.5 w-3.5" />
      </Button>
      {/* [2026-11-14 Feng] A text snippet has no "params → dispatch task" notion:
          it becomes "Copy" — one click takes the original text, no execution is created.
          The "Run" of the dispatch-task kind is unchanged, word for word. */}
      {row.sendMode === "text" ? (
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2"
          title={t("quickCmdsPage.action.copyTitle")}
          onClick={(e) => { e.stopPropagation(); void copyCmdText(row); }}
          data-testid={`quick-cmd-copy-${row.cmdId}`}
        >
          <Copy className="mr-1 h-3.5 w-3.5" />
          {t("quickCmdsPage.action.copy")}
        </Button>
      ) : (
        <Button
          size="sm"
          variant="run"
          className="h-7 px-2"
          title={t("quickCmdsPage.action.runTitle")}
          onClick={(e) => { e.stopPropagation(); setRunCmd(row); }}
          data-testid={`quick-cmd-run-${row.cmdId}`}
        >
          <Play className="mr-1 h-3.5 w-3.5" />
          {t("quickCmdsPage.action.run")}
        </Button>
      )}
      {/* [2026-11-04] Added data-testid: this "edit" was the only row action on the page without a testid,
          so the acceptance script could only click other pages by button order — change the order and it
          clicks the wrong thing and even reports a false functional bug. */}
      <Button
        size="sm"
        variant="edit"
        className="h-7 px-2"
        onClick={(e) => { e.stopPropagation(); setEditing(row); setFormOpen(true); }}
        data-testid={`quick-cmd-edit-${row.cmdId}`}
      >
        <Pencil className="h-3.5 w-3.5" />
      </Button>
      <Button size="sm" variant="destructive" className="h-7 px-2" onClick={(e) => { e.stopPropagation(); void handleArchive(row); }}>
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  );

  /**
   * Grid card (aligned with the "agent details → tasks" card pattern, structurally item by item):
   *   title row (badge + top-right row actions) → description (line-clamp-3) → bottom block (mt-auto, pinned)
   *   bottom block = the whole metadata row (wrapped in a border).
   * ★ The key to removing whitespace: do not use justify-between to push text and buttons to both ends;
   *   the bottom block uses mt-auto to pin to the bottom.
   */
  const renderCmdCard = (row: QuickCmd) => (
    <div
      className={cn("flex flex-col gap-2 rounded-lg border bg-card p-3", cardStateClass(cards.isSelected(row.cmdId)))}
      data-selected={cards.isSelected(row.cmdId) ? "true" : undefined}
      onClick={() => {
        cards.select(row.cmdId);
        navigate(`/quick-cmds/${encodeURIComponent(row.cmdId)}`);
      }}
      data-testid={`quick-cmd-card-${row.cmdId}`}
    >
      {/* [2026-11-05 21:49 Feng] "The action buttons are at the bottom... now they are bottom-right, move them
          to the top-right, unify this across the whole system"
          —— same pattern as the generic data-table grid: the title row splits into two columns, title left
             (min-w-0, truncatable) + buttons right (shrink-0, no wrapping). Buttons no longer occupy the
             last row → one row less per card, consistent with the 8 list pages across the site. */}
      <div className="flex items-start justify-between gap-2">
        <span
          className="min-w-0 truncate rounded-md bg-primary px-2 py-0.5 text-sm font-semibold text-primary-foreground shadow-sm"
          data-testid={`quick-cmd-card-title-${row.cmdId}`}
        >
          {row.title || row.cmdId}
        </span>
        <div
          className="flex shrink-0 items-center gap-1"
          onClick={(e) => e.stopPropagation()}
          data-testid={`quick-cmd-card-actions-${row.cmdId}`}
        >
          {renderCmdActions(row)}
        </div>
      </div>
      {/* cmdId / category / status / owner: small metadata right after the title (moved up from the metadata row,
   freeing that row for "metadata + buttons" together) */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-xs text-muted-foreground" data-testid={`quick-cmd-card-id-${row.cmdId}`}>
          {row.cmdId}
        </span>
        <SendModeBadge mode={row.sendMode} />
        <CategoryBadge category={row.category || "other"} />
        <StatusBadge active={row.activeStatus ?? 1} />
        <VisibilityBadge isPublic={row.isPublic} />
        <span className="text-xs text-muted-foreground" data-testid={`quick-cmd-card-owner-${row.cmdId}`}>
          {t("quickCmdsPage.card.executor", { who: executorLabel(row, t) })}
        </span>
      </div>
      <p className="line-clamp-3 text-sm leading-relaxed text-foreground/90" data-testid={`quick-cmd-card-desc-${row.cmdId}`}>
        {row.semanticSkeleton || t("quickCmdsPage.card.noDesc")}
      </p>
      {/* bottom block (mt-auto, pinned to the bottom): metadata only (row actions moved up to the title row's top right) */}
      <div className="mt-auto flex items-center gap-2 pt-1">
        <div
          className="inline-flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-md border border-border px-2 py-1 text-xs"
          data-testid={`quick-cmd-card-meta-${row.cmdId}`}
        >
          <span className="text-muted-foreground">
            {row.sendMode === "text" ? t("quickCmdsPage.card.textMode") : t("quickCmdsPage.card.taskMode")}
          </span>
          {row.sendMode === "text" ? (
            <span className="tabular-nums">
              {t("quickCmdsPage.card.chars", { n: (row.composedInstruction || "").length })}
            </span>
          ) : (
            <span className="font-mono">{row.targetTask || "—"}</span>
          )}
          <span className="text-border">|</span>
          <span className="text-muted-foreground">
            {t("quickCmdsPage.card.params", { n: row.params?.length ?? 0 })}
          </span>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-2" data-testid="page-quick-cmds">
      <div
        className="flex flex-col gap-4 rounded-xl border border-strong bg-card p-4 shadow-sm"
        data-testid="content-frame"
      >
        {/* [2026-10-28 16:32 Feng] Page header title/description removed (the nav already has them).
            [2026-10-29 Feng] The whole "current operating identity" row deleted (selected it filtered nothing,
            had no real effect); "New command" moved down to the filter row, on the same line as list/grid switch. */}
        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>
        )}
        {loading && <p className="text-sm text-muted-foreground">{t("quickCmdsPage.loading")}</p>}

        {!loading && (
          <DataTable
            /* [2026-11-02 Feng] Keep the user's conditions across a refresh (search/filter/page/page size/sort/view);
   without this line, a refresh returns to the initial state. */
            stateKey="quick-cmds"
            data={rows}
            rowKey={(r) => r.cmdId}
            columns={columns}
            searchKeys={["title", "cmdId", "ownerAgent", "targetTask", "semanticSkeleton", "category"]}
            searchPlaceholder={t("quickCmdsPage.searchPlaceholder")}
            /* [2026-10-29 Feng] "New command" moved here from the removed identity bar ——
               on the same row as the "list / grid" switch (DataTable toolbar slot sits right of the filter row). */
            toolbar={
              <Button
                onClick={() => { setEditing(null); setFormOpen(true); }}
                data-testid="quick-cmd-new"
              >
                <Plus className="mr-1 h-4 w-4" />
                {t("quickCmdsPage.newCmd")}
              </Button>
            }
            filters={[
              {
                key: "sendMode",
                label: t("quickCmdsPage.filter.kind"),
                /* existing rows without send_mode are already normalized to task by the backend; the filter falls back to task
                   as well, otherwise "dispatch task" would filter out all old data. */
                options: [
                  { label: t("quickCmdsPage.mode.task"), value: "task" },
                  { label: t("quickCmdsPage.mode.text"), value: "text" },
                ],
                getValue: (r) => r.sendMode || "task",
              },
              {
                key: "category",
                label: t("quickCmdsPage.filter.category"),
                options: Array.from(new Set(rows.map((r) => r.category || "other"))).map((c) => ({ label: categoryLabel(c, t), value: c })),
              },
              {
                key: "ownerAgent",
                label: "Agent",
                options: agentOptions,
              },
              {
                key: "__project",
                label: t("quickCmdsPage.filter.project"),
                options: projectOptions,
                getValue: (r) => projectOfCmd(r),
              },
            ]}
            defaultView="card"
            emptyText={t("quickCmdsPage.emptyText")}
            onRowClick={(row) => navigate(`/quick-cmds/${encodeURIComponent(row.cmdId)}`)}
            rowActions={renderCmdActions}
            renderCard={renderCmdCard}
            /* the card holds 3 row action buttons + metadata; with 3 columns the bottom "metadata row + buttons" gets
               squeezed into two lines → this page's grid uses 2 columns, keeping "target task xxx | params N"
               on the same line as the row actions. */
            cardGridClassName="grid gap-3 sm:grid-cols-2"
          />
        )}

        {/* [2026-10-26 FIX] The new/edit form was never mounted → "New command" and "Edit" did nothing when clicked.
            The component was missed in the refactor to a standalone detail page; restored here. */}
        <QuickCmdForm
          open={formOpen}
          initial={editing}
          onOpenChange={(o) => {
            setFormOpen(o);
            if (!o) setEditing(null);
          }}
          onSaved={() => void load()}
        />

        {/* run dialog: uses the generic "message send" window (instruction and executor auto-locked by public/private) */}
        <QuickCmdSendDialog
          cmd={runCmd}
          open={!!runCmd}
          onOpenChange={(o) => { if (!o) setRunCmd(null); }}
        />

      </div>
    </div>
  );
}
