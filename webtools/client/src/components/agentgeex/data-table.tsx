/**
 * components/agentgeex/data-table.tsx — schema-driven generic data table
 *
 * Positioning (per agentgeex_ui.md §1.1 / §3):
 *   - every "list module" uses the same wrapper: column definition array → render, with built-in
 *     search / filter / pagination / inline actions;
 *   - supports Table ⇄ Card (grid) dual view switching;
 *   - avoids repeating CRUD template code across pages.
 *
 * ⚠️ Pagination rules (2026-10-03 Feng's instruction):
 *   UI invents nothing. Pagination **strictly matches the shardkit example**
 *   (ui_component_library/src/pages/cases/page-crud/index.tsx):
 *     - use the ui/Pagination component (page numbers + prev/next + page-size selector + total display)
 *     - state shape: page / pageSize (including setPageSize)
 *     - setPage(1) when filter/search/pageSize changes (React.useEffect)
 *     - deleting can push the page out of range → clamp to the last page
 *     - bottom left "N total · rows a–b", right <Pagination .../>
 */
import * as React from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Search, LayoutGrid, List, Inbox } from "lucide-react";
import { cn } from "@/lib/utils";
import { cardStateClass, useCardSelection } from "@/lib/card-selection";
import { readState, usePageState, writeState } from "@/lib/page-state";
import { EmptyState } from "@/components/ui/empty-state";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { OnlineDot } from "@/components/ui/online-dot";
import { Pagination } from "@/components/ui/pagination";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "@/components/ui/table";

/* ------------------------------ types ------------------------------ */
export interface ColumnDef<T> {
  /** column id (also the key used to read the field) */
  key: string;
  /** header label */
  header: string;
  /** cell renderer */
  render?: (row: T) => React.ReactNode;
  /** whether sortable (array sorting) */
  sortable?: boolean;
  /** [2026-11-05] sort value (optional).
   *  Default is sorting by `row[key]`; derived columns (key is not a real field, e.g. sorting by
   *  runtime.contextRatio) must provide this, otherwise both sides are undefined → clicking sort does
   *  nothing and it looks like "this column is not sortable".
   *  Return a number to sort numerically, a string to sort as localized strings. */
  sortValue?: (row: T) => string | number;
  /** table column width / field used as subtitle in card view */
  className?: string;
  /** whether it is the main title in card view (only the first one is used) */
  cardTitle?: boolean;
  /** used as subtitle in card view */
  cardSubtitle?: boolean;
  /** [2026-11-05 19:20 Feng] card-view-only renderer (optional).
   *  Why: `render` is for table cells (often with icons, column width, truncate) and looks heavy when moved
   *  into a card title/subtitle slot (e.g. the task "description" column carries an EntityLogo).
   *  Use it when provided, fall back to `render` otherwise.
   *  Typical: desktop table wants icons, card title wants a single line of plain text. */
  cardRender?: (row: T) => React.ReactNode;
  /** [2026-11-18] hide only in **card view**; table view always renders all columns.
   *  Past pitfall: the table header/cells were also filtered by this field, so columns marked hideInCard
   *  disappeared entirely in table view too (notices visibility / skills tags / hosts description all hit).
   *  If "hide in table too" is ever really needed, add a separate field (e.g. hideInTable),
   *  do not reuse this one — one field expresses one thing. */
  hideInCard?: boolean;
}

export interface FilterDef<T> {
  key: string;
  label: string;
  /** options; no filtering when the value is "all".
   *  `online` [2026-11-07 Feng]: mark online status in the host dropdown — green dot when online, none when offline.
   *  Put on FilterDef instead of each page composing its own label because **every list page's filter goes
   *  through this component**: one addition applies site-wide, and no page has a green dot while another does not. */
  options: { label: string; value: string; online?: boolean }[];
  /** value getter (default row[key]) */
  getValue?: (row: T) => string;
  /** [2026-10-26] multi-value matching: getValue returns a comma-separated set, keep the row when any of them matches.
   *  For "one row relates to multiple values" (e.g. a project spanning hosts → hosts = "test_host_mac,test_srv_a").
   *  Default false = strict equality (existing behaviour unchanged). */
  multi?: boolean;
}

interface DataTableProps<T extends Record<string, any>> {
  /** data source */
  data: T[];
  /** column definitions */
  columns: ColumnDef<T>[];
  /** unique row key (default row.id) */
  rowKey?: (row: T) => string;
  /** search: keyword-match against these fields */
  searchKeys?: string[];
  /** search box placeholder */
  searchPlaceholder?: string;
  /** filter definitions */
  filters?: FilterDef<T>[];
  /** default view */
  defaultView?: "table" | "card";
  /** whether view switching is allowed */
  allowViewToggle?: boolean;
  /** row actions (end of table row / bottom of card) */
  rowActions?: (row: T) => React.ReactNode;
  /** page size (initial value) */
  pageSize?: number;
  /** page size options (aligned with the shardkit example) */
  pageSizeOptions?: number[];
  /** empty-state copy */
  emptyText?: string;
  /** extra toolbar (left side) */
  toolbar?: React.ReactNode;
  /** row click */
  onRowClick?: (row: T) => void;
  /** [2026-10-16] custom grid card renderer (optional).
   *  When provided, card view uses its return value as the card (the outer grid stays the same);
   *  otherwise the built-in generic card is used. For "the card look must exactly match a page"
   *  (e.g. quick commands aligned with the task page's large grid), without changing the built-in card
   *  and affecting other list pages. */
  renderCard?: (row: T) => React.ReactNode;
  /** [2026-10-29] initial filter values (optional).
   *  For "navigation with params": when another place links here with `?agent=xxx` to preset a filter,
   *  the page converts it to { key: value }; the dropdown shows it and the list is already filtered.
   *  **Applies on mount only** (aligned with filterVals' initial state); later changes use the dropdown. */
  initialFilterVals?: Record<string, string>;
  /** [2026-11-02] Keep the conditions the user set across a refresh (Feng).
   *  Pass a **page-unique** short name (e.g. `"executions"`) and this table stores search term / filter /
   *  page / page size / sort / view in `lib/page-state.ts`.
   *  Why: the navbar refresh button does "re-pull snapshot + remount the whole page", and remounting clears
   *  all of these — so "refresh data" turns into "refresh data + forget what I was looking at".
   *  Omitted = old behaviour (back to the initial state after refresh). */
  stateKey?: string;
  /** [2026-10-16] grid class names (optional). Default `grid gap-3 sm:grid-cols-2 lg:grid-cols-3`;
   *  pages with heavier card content (e.g. several row action buttons) can widen the columns
   *  (e.g. only `sm:grid-cols-2`) so the bottom "metadata row + row actions" is not squeezed into two lines. */
  cardGridClassName?: string;
}

/* ------------------------------ component ------------------------------ */
export function DataTable<T extends Record<string, any>>({
  data,
  columns,
  rowKey = (r) => String(r.id ?? r.run_uuid ?? Math.random()),
  searchKeys = ["name"],
  searchPlaceholder,
  filters = [],
  initialFilterVals,
  stateKey,
  defaultView = "table",
  allowViewToggle = true,
  rowActions,
  pageSize: initialPageSize = 10,
  pageSizeOptions = [10, 20, 30, 50, 100, 500],
  emptyText,
  toolbar,
  onRowClick,
  renderCard,
  cardGridClassName = "grid gap-3 sm:grid-cols-2 lg:grid-cols-3",
}: DataTableProps<T>) {
  const { t } = useTranslation();
  /* ★ These six states are exactly "the conditions the user set". With stateKey they survive remounts.
     Without stateKey, usePageState degrades to a plain useState, byte-for-byte the old behaviour. */
  const sk = stateKey ? `${stateKey}:` : undefined;

  /* [2026-11-05 12:54 Feng] "Except for agents, you did not implement it anywhere else — the selected state on click"
     —— previously the selected state was only built into the **custom cards** of the agent page and the
        quick command page; this generic table's own grid cards (sessions / tasks / projects / plans / skills /
        notices / executions / hosts... all use it) just navigated away and left nothing on return.
     Now it is inside the component: every "grid + whole-card clickable" list gets the same selected state
     automatically, with zero changes per page (storageKey uses the page's own stateKey, else the path).
     Rules match @/lib/card-selection: what hover looks like is what stays after clicking; remembered for 30 min. */
  const { pathname } = useLocation();
  const cards = useCardSelection({
    storageKey: ["agentgeex", "cardSel", stateKey || pathname.replace(/\//g, "_") || "root"].join("."),
    testIdPrefix: "dt-card-",
  });

  /* Table rows and grid cards both go through here: record the selection first, then navigate —
     otherwise "clicked in table view, no selection visible when switching back to grid" makes users think
     it was not remembered. */
  const handleRowClick = React.useCallback(
    (row: T) => {
      if (!onRowClick) return;
      cards.select(rowKey(row));
      onRowClick(row);
    },
    [onRowClick, cards, rowKey],
  );

  const [view, setView] = usePageState<"table" | "card">(sk && `${sk}view`, defaultView);
  const [q, setQ] = usePageState(sk && `${sk}q`, "");
  const [filterVals, setFilterVals] = usePageState<Record<string, string>>(
    sk && `${sk}filters`,
    initialFilterVals ?? {},
  );
  const [sort, setSort] = usePageState<{ key: string; dir: "asc" | "desc" } | null>(sk && `${sk}sort`, null);
  const [page, setPage] = usePageState(sk && `${sk}page`, 1);
  const [pageSize, setPageSize] = usePageState(sk && `${sk}pageSize`, initialPageSize);

  /* ── URL-param navigation vs restored memory (see the header note in lib/page-state.ts) ──
     Criterion: remember "the URL params already applied"; if they are literally the same, do not apply again —
     a remount after refresh keeps the same URL, and it must not push back the filter the user changed later;
     but really arriving from elsewhere with new params (e.g. ?agent=B) differs literally, so it overrides normally. */
  const urlFiltersJson = React.useMemo(
    () =>
      initialFilterVals && Object.keys(initialFilterVals).length
        ? JSON.stringify(initialFilterVals)
        : "",
    // decided once at mount only: runtime URL changes are handled by the page re-navigating itself
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  React.useLayoutEffect(() => {
    if (!sk || !urlFiltersJson) return;
    if (readState<string>(`${sk}urlFilters`) === urlFiltersJson) return;
    writeState(`${sk}urlFilters`, urlFiltersJson);
    setFilterVals(initialFilterVals ?? {});
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // filter + search + sort
  const filtered = React.useMemo(() => {
    let rows = data;
    const kw = q.trim().toLowerCase();
    if (kw) {
      rows = rows.filter((r) =>
        searchKeys.some((k) => String(r[k] ?? "").toLowerCase().includes(kw))
      );
    }
    for (const f of filters) {
      const v = filterVals[f.key];
      if (!v || v === "__all__") continue;
      rows = rows.filter((r) => {
        const raw = String((f.getValue ? f.getValue(r) : r[f.key]) ?? "");
        if (!f.multi) return raw === v;
        // multi: comma-separated set, keep a row when any value matches (a project across hosts, etc.)
        return raw
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean)
          .includes(v);
      });
    }
    if (sort) {
      const col = columns.find((c) => c.key === sort.key);
      const pick = col?.sortValue;
      rows = [...rows].sort((a, b) => {
        const av = pick ? pick(a) : a[sort.key];
        const bv = pick ? pick(b) : b[sort.key];
        if (typeof av === "number" && typeof bv === "number")
          return (av - bv) * (sort.dir === "asc" ? 1 : -1);
        return String(av ?? "").localeCompare(String(bv ?? "")) * (sort.dir === "asc" ? 1 : -1);
      });
    }
    return rows;
  }, [data, q, searchKeys, filters, filterVals, sort, columns]);

  // align with shardkit: filter / search / page-size change → back to page 1
  /* Conditions changed → back to page 1 — but **the first mount does not count as a change**:
     otherwise on a refresh remount the just-restored page number is instantly reset to 1, pointless storage. */
  const firstRun = React.useRef(true);
  React.useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    setPage(1);
    // note: setPage is not included (it is a stable useCallback, and including it breaks "jump only once")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, filterVals, sort, pageSize]);

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, totalPages);
  const pageRows = filtered.slice((current - 1) * pageSize, current * pageSize);

  // align with shardkit: page out of range (e.g. after deletion) → clamp to the last page
  React.useEffect(() => {
    setPage((p) => Math.min(p, totalPages));
  }, [totalPages]);

  const toggleSort = (key: string) => {
    setSort((s) =>
      s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }
    );
  };

  const titleCol = columns.find((c) => c.cardTitle) ?? columns[0];
  const subtitleCol = columns.find((c) => c.cardSubtitle);

  return (
    <div className="flex flex-col gap-3" data-testid="agx-data-table">
      {/* toolbar: search + filter + view switch */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={searchPlaceholder ?? t("common.searchPlaceholder")}
            aria-label={searchPlaceholder ?? t("common.searchPlaceholder")}
            className="h-9 w-56 pl-8"
            data-testid="agx-search"
          />
        </div>

        {filters.map((f) => (
          <Select
            key={f.key}
            value={filterVals[f.key] ?? "__all__"}
            onValueChange={(v) => setFilterVals((p) => ({ ...p, [f.key]: v }))}
          >
            <SelectTrigger className="h-9 w-36" aria-label={f.label} data-testid={`agx-filter-${f.key}`}>
              {/* [2026-10-29] Pass children explicitly: when "the value has no registered item", Radix
                  shows the raw value (e.g. entering with `?agent=agx_core` shows the id, not the name);
                  here the label is looked up from options, keeping the same "label: value" format as other filters. */}
              <SelectValue placeholder={f.label}>
                {(() => {
                  const v = filterVals[f.key];
                  if (!v || v === "__all__") return t("common.allOption", { label: f.label });
                  return f.options.find((o) => o.value === v)?.label ?? v;
                })()}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {/* "All" also reserves a dot slot — otherwise its siblings with a green dot all shift right by 8px
                  and the left edge of the column text looks jagged (a real issue caught in screenshot review). */}
              <SelectItem value="__all__">
                <span className="inline-flex items-center gap-1.5">
                  <OnlineDot />
                  {t("common.allOption", { label: f.label })}
                </span>
              </SelectItem>
              {f.options.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {/* the green dot appears only for "online/running"; offline options stay plain, no grey dot —
                      a row of grey dots renders "no information" as "everything is bad". */}
                  <span className="inline-flex items-center gap-1.5">
                    <OnlineDot show={o.online} title={o.online ? t("status.online") : undefined} />
                    {o.label}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ))}

        <div className="ml-auto flex items-center gap-2">
          {toolbar}
          {allowViewToggle && (
            <div className="flex items-center rounded-md border p-0.5" role="group" aria-label={t("miscShell.dataTable.viewSwitch")}>
              <Button
                variant={view === "table" ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2"
                onClick={() => setView("table")}
                title={t("miscShell.dataTable.tableView")}
                aria-label={t("miscShell.dataTable.tableView")}
                data-testid="agx-view-table"
              >
                <List className="h-4 w-4" />
              </Button>
              <Button
                variant={view === "card" ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2"
                onClick={() => setView("card")}
                title={t("miscShell.dataTable.cardView")}
                aria-label={t("miscShell.dataTable.cardView")}
                data-testid="agx-view-card"
              >
                <LayoutGrid className="h-4 w-4" />
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* content area */}
      {pageRows.length === 0 ? (
        <div className="rounded-lg border border-dashed">
          <EmptyState icon={Inbox} title={emptyText ?? t("common.noData")} />
        </div>
      ) : view === "table" ? (
        /* white background for the content surface: after the four-region rework the canvas is grey, and a
     transparent table would just "sink" into the canvas */
        <div className="rounded-lg border bg-card">
          <Table data-testid="agx-table-view">
            <TableHeader>
              <TableRow>
                {columns.map((c) => (
                  <TableHead
                    key={c.key}
                    className={cn(c.sortable && "cursor-pointer select-none", c.className)}
                    onClick={c.sortable ? () => toggleSort(c.key) : undefined}
                  >
                    <span className="inline-flex items-center gap-1">
                      {c.header}
                      {c.sortable && sort?.key === c.key && (
                        <span className="text-xs">{sort.dir === "asc" ? "↑" : "↓"}</span>
                      )}
                    </span>
                  </TableHead>
                ))}
                {rowActions && <TableHead className="w-[1%] text-right">{t("common.actions")}</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody striped>
              {pageRows.map((row) => (
                <TableRow
                  key={rowKey(row)}
                  className={cn(onRowClick && "cursor-pointer")}
                  onClick={onRowClick ? () => handleRowClick(row) : undefined}
                >
                  {columns.map((c) => (
                    <TableCell key={c.key} className={c.className}>
                      {c.render ? c.render(row) : String(row[c.key] ?? "—")}
                    </TableCell>
                  ))}
                  {rowActions && (
                    <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                      {rowActions(row)}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className={cardGridClassName} data-testid="agx-card-view">
          {pageRows.map((row) =>
            renderCard ? (
              <React.Fragment key={rowKey(row)}>{renderCard(row)}</React.Fragment>
            ) : (
            <Card
              key={rowKey(row)}
              data-testid={`dt-card-${rowKey(row)}`}
              data-selected={onRowClick && cards.isSelected(rowKey(row)) ? "true" : undefined}
              /* [2026-11-05 12:54] hover and selected share the same visuals (cardStateClass), applied only when the
                  whole card is clickable: a non-clickable card gets no hover, avoiding "looks clickable,
                  click does nothing". */
              className={onRowClick ? cardStateClass(cards.isSelected(rowKey(row))) : undefined}
              onClick={onRowClick ? () => handleRowClick(row) : undefined}>
              <CardContent className="flex flex-col gap-2 p-4">
                {/* [2026-11-05 19:20 Feng] "An edit button in the empty space pulls the data down and adds a row below;
                    move the buttons to the top-right parallel to the title — on the agent page all buttons are top-right"
                    —— the title row becomes two columns: title on the left (min-w-0, truncatable) + buttons on the
                       right (shrink-0, no wrapping); buttons no longer take their own row, one row less per card.
                       Every list site shares this one card, so one change applies everywhere. */}
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold">
                      {titleCol.cardRender
                        ? titleCol.cardRender(row)
                        : titleCol.render
                          ? titleCol.render(row)
                          : String(row[titleCol.key] ?? "—")}
                    </div>
                    {subtitleCol && (
                      /* subtitle truncated to one line → add a native title tooltip so hovering shows the whole text
     (truncate with no fallback = cannot be read in full) */
                      <div
                        className="truncate text-xs text-muted-foreground"
                        title={
                          typeof row[subtitleCol.key] === "string"
                            ? String(row[subtitleCol.key])
                            : undefined
                        }
                      >
                        {subtitleCol.cardRender
                          ? subtitleCol.cardRender(row)
                          : subtitleCol.render
                            ? subtitleCol.render(row)
                            : String(row[subtitleCol.key] ?? "")}
                      </div>
                    )}
                  </div>
                  {rowActions && (
                    /* stopPropagation: the button sits inside the whole-card clickable area, without this it also opens details */
                    <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      {rowActions(row)}
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
                  {columns
                    .filter((c) => c.key !== titleCol.key && c.key !== subtitleCol?.key && !c.hideInCard)
                    .slice(0, 4)
                    .map((c) => (
                      <span key={c.key}>
                        <span className="opacity-60">{c.header}：</span>
                        {c.cardRender
                          ? c.cardRender(row)
                          : c.render
                            ? c.render(row)
                            : String(row[c.key] ?? "—")}
                      </span>
                    ))}
                </div>
              </CardContent>
            </Card>
            )
          )}
        </div>
      )}

      {/* pagination = shardkit ui/Pagination component (page numbers + prev/next + page-size selector + total) */}
      <div className="pt-1" data-testid="agx-total" data-total={total} data-page={current} data-page-size={pageSize}>
        <Pagination
          page={current}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
          pageSizeOptions={pageSizeOptions}
        />
      </div>
    </div>
  );
}

/** status badge (shared by all list pages) */
export function StatusPill({ status, className }: { status: string; className?: string }) {
  const { t } = useTranslation();
  /* ★ Copy lives in the language pack (`status.<value>`); only key + color stay here.
     Previously the labels were written in Chinese — one change meant syncing 18 values, and the English UI
     would inevitably miss some. */
  const map: Record<string, { labelKey: string; className: string }> = {
    online: { labelKey: "status.online", className: "bg-success/15 text-success border-success/30" },
    busy: { labelKey: "status.busy", className: "bg-warning/15 text-warning border-warning/30" },
    offline: { labelKey: "status.offline", className: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30" },
    error: { labelKey: "status.error", className: "bg-destructive/15 text-destructive border-destructive/30" },
    active: { labelKey: "status.active", className: "bg-success/15 text-success border-success/30" },
    idle: { labelKey: "status.idle", className: "bg-info/15 text-info border-info/30" },
    archived: { labelKey: "status.archived", className: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30" },
    paused: { labelKey: "status.paused", className: "bg-warning/15 text-warning border-warning/30" },
    running: { labelKey: "status.running", className: "bg-info/15 text-info border-info/30" },
    /* [2026-11-03 21:43 Feng] Execution status copy **must use the same words** as the dashboard "task overview":
       pending = queued (not "to be processed" — it does not mean "waiting for a human", only queued for a
       prerequisite or a schedule),
       succeeded = succeeded (aligned with the dashboard wording, otherwise one thing has two names). */
    succeeded: { labelKey: "status.succeeded", className: "bg-success/15 text-success border-success/30" },
    failed: { labelKey: "status.failed", className: "bg-destructive/15 text-destructive border-destructive/30" },
    pending: { labelKey: "status.pending", className: "bg-warning/15 text-warning border-warning/30" },
    /* [2026-11-06 Feng] New status "awaiting human confirmation" (awaiting_confirm).
       ★ Use **the same words** as the old pending_confirm: both values mean the same thing, and calling one
         "awaiting human confirmation" and the other "pending confirmation" makes users search for two states.
       Color borrowed from chart-1 (purple) — "needs a human" must not blend with failure (red) / queued (yellow). */
    awaiting_confirm: { labelKey: "status.awaiting_confirm", className: "bg-chart-1/15 text-chart-1 border-chart-1/30" },
    pending_confirm: { labelKey: "status.awaiting_confirm", className: "bg-chart-1/15 text-chart-1 border-chart-1/30" },
    draft: { labelKey: "status.draft", className: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30" },
    cancelled: { labelKey: "status.cancelled", className: "bg-muted-foreground/15 text-muted-foreground border-muted-foreground/30" },
    completed: { labelKey: "status.completed", className: "bg-success/15 text-success border-success/30" },
    /* ★ Added 2026-11-03: the real plan status (agx_plan.plan_status):
       activated is "in progress" and must be visually equivalent to running — it must not fall into draft grey. */
    activated: { labelKey: "status.activated", className: "bg-info/15 text-info border-info/30" },
    cancelling: { labelKey: "status.cancelling", className: "bg-warning/15 text-warning border-warning/30" },
  };
  const m = map[status] ?? { labelKey: "", className: "bg-muted text-muted-foreground" };
  return (
    <Badge variant="outline" className={cn("font-normal", m.className, className)} data-testid={`status-${status}`}>
      {m.labelKey ? t(m.labelKey) : status}
    </Badge>
  );
}
