import * as React from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Lightweight calendar — zero external dependencies, tech-styled gradient selected state.
 * Compared with react-day-picker: more control, smaller footprint, styling fully through Tailwind theme tokens.
 *
 * Supports three views (view):
 *   - date  day view (default)
 *   - month month view (pick a month)
 *   - year  year view (pick a year, paginated 12 years)
 * Supports range selection (range): pass a selected array [start, end], highlights the selection.
 * Supports disabled dates (disabledDate): returning true means not selectable.
 */
export type CalendarView = "date" | "month" | "year";
export type DateRange = [Date | undefined, Date | undefined];

export interface CalendarProps {
  /** Single value */
  value?: Date;
  /** Single-select callback */
  onChange?: (d: Date) => void;
  /** Range value [start, end]; passing it enters range-selection mode */
  selected?: DateRange;
  /** Range callback */
  onSelectRange?: (r: DateRange) => void;
  /** View: day/month/year */
  view?: CalendarView;
  /** Disabled dates */
  disabledDate?: (d: Date) => boolean;
  /** Year jump step, default 1 */
  yearStep?: number;
  /** Base date for the controlled "currently displayed month/year" (used to share the month across both panels); when omitted it is managed internally */
  baseMonth?: Date;
  /** Displayed month change callback (used with baseMonth for two-panel linkage) */
  onMonthChange?: (d: Date) => void;
  /** Force the "range endpoint" role of the selected state; only for the two-panel layout: left panel = start endpoint, right = end endpoint */
  edgeRole?: "start" | "end";
  /** Controlled starting point of "range picking" (shared by both panels); when omitted it is managed internally */
  pendingStart?: Date | null;
  /** Callback when a date is clicked (takes over selection logic in two-panel mode) */
  onPickDate?: (d: Date) => void;
  /** Hide the header navigation (in two-panel mode only the right panel keeps paging) */
  hideNav?: boolean;
  /** Hide the header arrow buttons (keep the title); used when the two-panel layout lifts the arrows to the two top sides */
  hideArrows?: boolean;
  /** Extra className passed to the grid day cells */
  className?: string;
}

/** Weekday initials / month names: key table (hooks are unavailable at module load, values are resolved inside the component) */
const WEEK_KEYS = [
  "datePicker.week.sun",
  "datePicker.week.mon",
  "datePicker.week.tue",
  "datePicker.week.wed",
  "datePicker.week.thu",
  "datePicker.week.fri",
  "datePicker.week.sat",
];
const MONTH_KEYS = [
  "datePicker.month.m1",
  "datePicker.month.m2",
  "datePicker.month.m3",
  "datePicker.month.m4",
  "datePicker.month.m5",
  "datePicker.month.m6",
  "datePicker.month.m7",
  "datePicker.month.m8",
  "datePicker.month.m9",
  "datePicker.month.m10",
  "datePicker.month.m11",
  "datePicker.month.m12",
];

const sameDay = (a?: Date, b?: Date) =>
  !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const sameMonth = (a?: Date, b?: Date) => !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth();
const sameYear = (a?: Date, b?: Date) => !!a && !!b && a.getFullYear() === b.getFullYear();

const NavBtn = ({
  onClick, label, testid, children,
}: { onClick: () => void; label: string; testid: string; children: React.ReactNode }) => (
  <button
    type="button"
    onClick={onClick}
    aria-label={label}
    data-testid={testid}
    className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
  >
    {children}
  </button>
);

export function Calendar({
  value = new Date(),
  onChange,
  selected,
  onSelectRange,
  view = "date",
  disabledDate,
  className,
  yearStep = 1,
  baseMonth,
  onMonthChange,
  edgeRole,
  pendingStart,
  onPickDate,
  hideNav,
  hideArrows,
}: CalendarProps) {
  const { t, i18n } = useTranslation();
  const weekLabels = WEEK_KEYS.map((k) => t(k));
  const monthLabels = MONTH_KEYS.map((k) => t(k));
  const rangeMode = !!selected;
  const anchor = value ?? selected?.[0] ?? new Date();
  const [innerViewDate, setInnerViewDate] = React.useState(() => new Date(anchor.getFullYear(), anchor.getMonth(), 1));
  // Controlled mode: if baseMonth is passed from outside, use it (two-panel sharing/linkage)
  const viewDate = baseMonth ?? innerViewDate;
  const setViewDate = (d: Date) => {
    if (baseMonth) onMonthChange?.(d);
    else setInnerViewDate(d);
  };
  // Paging origin of the year view (12 years per screen)
  const [yearBase, setYearBase] = React.useState(() => Math.floor(anchor.getFullYear() / 12) * 12);

  const y = viewDate.getFullYear();
  const m = viewDate.getMonth();
  const today = new Date();

  // Internal range-selection state (click the first point, click the second to close)
  const [rangePicking, setRangePicking] = React.useState<Date | null>(null);
  React.useEffect(() => {
    if (rangeMode && selected?.[1]) setRangePicking(null);
  }, [rangeMode, selected]);

  /* ── Header navigation ── */
  const header = (() => {
    if (view === "date") {
      return {
        /* Chinese uses the numeric month ("2026" + "11" + month suffix), English uses the month name ("Nov 2026").
            Keep placeholders identical on both sides ({{y}} / {{mLabel}}) so i18n:check can compare them. */
        title: t("datePicker.title.yearMonth", {
          y,
          mLabel: i18n.language.startsWith("zh") ? String(m + 1) : monthLabels[m],
        }),
        titleTestid: "cal-title",
        onPrevMonth: () => setViewDate(new Date(y, m - 1, 1)),
        onNextMonth: () => setViewDate(new Date(y, m + 1, 1)),
        onPrevYear: () => setViewDate(new Date(y - yearStep, m, 1)),
        onNextYear: () => setViewDate(new Date(y + yearStep, m, 1)),
      };
    }
    if (view === "month") {
      return {
        title: t("datePicker.title.year", { y }),
        titleTestid: "cal-title",
        onPrevMonth: () => setViewDate(new Date(y - 1, m, 1)),
        onNextMonth: () => setViewDate(new Date(y + 1, m, 1)),
        onPrevYear: () => setViewDate(new Date(y - 10, m, 1)),
        onNextYear: () => setViewDate(new Date(y + 10, m, 1)),
        isYearGroup: true,
      };
    }
    // year
    return {
      title: `${yearBase} - ${yearBase + 11}`,
      titleTestid: "cal-title",
      onPrevMonth: () => setYearBase((b) => b - 12),
      onNextMonth: () => setYearBase((b) => b + 12),
      onPrevYear: () => setYearBase((b) => b - 120),
      onNextYear: () => setYearBase((b) => b + 120),
      isYearGroup: true,
    };
  })();

  /* ── Day view cell ── */
  const dayCells: { date: Date; current: boolean }[] = [];
  if (view === "date") {
    const firstDay = new Date(y, m, 1).getDay();
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const daysInPrev = new Date(y, m, 0).getDate();
    for (let i = firstDay - 1; i >= 0; i--) dayCells.push({ date: new Date(y, m - 1, daysInPrev - i), current: false });
    for (let d = 1; d <= daysInMonth; d++) dayCells.push({ date: new Date(y, m, d), current: true });
    while (dayCells.length % 7 !== 0)
      dayCells.push({ date: new Date(y, m + 1, dayCells.length - firstDay - daysInMonth + 1), current: false });
  }

  const pick = (d: Date) => {
    if (disabledDate?.(d)) return;
    if (onPickDate) {
      // Two-panel mode: selection logic is handled by the parent
      if (rangeMode) onSelectRange?.([d, undefined]);
      onPickDate(d);
      return;
    }
    if (rangeMode) {
      if (!rangePicking) {
        setRangePicking(d);
        onSelectRange?.([d, undefined]);
      } else {
        const [a, b] = rangePicking <= d ? [rangePicking, d] : [d, rangePicking];
        setRangePicking(null);
        onSelectRange?.([a, b]);
      }
      return;
    }
    // Month view: select the first day of the whole month; year view: select Jan 1 of that year
    onChange?.(d);
  };

  // Highlight check for in-range dates
  const activePending = pendingStart !== undefined ? pendingStart : rangePicking;
  const start = activePending ?? selected?.[0];
  const end = activePending ? undefined : selected?.[1];
  const inRange = (d: Date) => {
    if (!rangeMode || !start || !end) return false;
    return d > start && d < end;
  };
  const isEdge = (d: Date) => sameDay(d, start) || sameDay(d, end);

  return (
    <div className={cn("w-[268px] select-none", className)}>
      <div className={cn("mb-2 flex items-center justify-between", hideNav && "hidden")}>
        {!hideArrows && (
          <div className="flex items-center gap-0.5">
            <NavBtn onClick={header.onPrevYear} label={header.isYearGroup ? t("datePicker.nav.prevDecade") : t("datePicker.nav.prevYear")} testid="cal-prev-year">
              <ChevronsLeft className="h-4 w-4" />
            </NavBtn>
            <NavBtn onClick={header.onPrevMonth} label={header.isYearGroup ? t("datePicker.nav.prevYear") : t("datePicker.nav.prevMonth")} testid="cal-prev-month">
              <ChevronLeft className="h-4 w-4" />
            </NavBtn>
          </div>
        )}
        <div className={cn("text-sm font-semibold", hideArrows && "mx-auto")} data-testid={header.titleTestid}>{header.title}</div>
        {!hideArrows && (
          <div className="flex items-center gap-0.5">
            <NavBtn onClick={header.onNextMonth} label={header.isYearGroup ? t("datePicker.nav.nextYear") : t("datePicker.nav.nextMonth")} testid="cal-next-month">
              <ChevronRight className="h-4 w-4" />
            </NavBtn>
            <NavBtn onClick={header.onNextYear} label={header.isYearGroup ? t("datePicker.nav.nextDecade") : t("datePicker.nav.nextYear")} testid="cal-next-year">
              <ChevronsRight className="h-4 w-4" />
            </NavBtn>
          </div>
        )}
      </div>

      {/* ── Month view ── */}
      {view === "month" && (
        <div className="grid grid-cols-3 gap-1" data-testid="cal-month-grid">
          {monthLabels.map((label, i) => {
            const md = new Date(y, i, 1);
            const active = sameMonth(md, selected?.[0] ?? value);
            const disabled = disabledDate?.(md);
            return (
              <button
                type="button"
                key={label}
                disabled={disabled}
                data-testid={`cal-month-${i + 1}`}
                onClick={() => pick(md)}
                className={cn(
                  "flex h-9 items-center justify-center rounded-md text-sm transition-colors",
                  disabled && "cursor-not-allowed text-muted-foreground/30",
                  active && !disabled && "bg-gradient-to-br from-sky-500 to-indigo-500 font-semibold text-white shadow-[0_0_12px_-2px_rgba(99,102,241,0.8)]",
                  !active && !disabled && "hover:bg-accent"
                )}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}

      {/* ── Year view ── */}
      {view === "year" && (
        <div className="grid grid-cols-3 gap-1" data-testid="cal-year-grid">
          {Array.from({ length: 12 }, (_, i) => yearBase + i).map((yy) => {
            const yd = new Date(yy, 0, 1);
            const active = sameYear(yd, selected?.[0] ?? value);
            const disabled = disabledDate?.(yd);
            return (
              <button
                type="button"
                key={yy}
                disabled={disabled}
                data-testid={`cal-year-${yy}`}
                onClick={() => pick(yd)}
                className={cn(
                  "flex h-9 items-center justify-center rounded-md text-sm transition-colors",
                  disabled && "cursor-not-allowed text-muted-foreground/30",
                  active && !disabled && "bg-gradient-to-br from-sky-500 to-indigo-500 font-semibold text-white shadow-[0_0_12px_-2px_rgba(99,102,241,0.8)]",
                  !active && !disabled && "hover:bg-accent"
                )}
              >
                {yy}
              </button>
            );
          })}
        </div>
      )}

      {/* ── Day view ── */}
      {view === "date" && (
        <>
          <div className="grid grid-cols-7 gap-0.5 text-center text-[11px] text-muted-foreground">
            {weekLabels.map((w, i) => <div key={i} className="py-1">{w}</div>)}
          </div>
          <div className="grid grid-cols-7 gap-0.5">
            {dayCells.map((c, i) => {
              const disabled = disabledDate?.(c.date);
              const active = rangeMode
                ? (edgeRole === "start" && !activePending
                    ? sameDay(c.date, start)
                    : edgeRole === "start" && activePending
                    ? sameDay(c.date, activePending)
                    : edgeRole === "end"
                    ? sameDay(c.date, end)
                    : isEdge(c.date))
                : sameDay(c.date, value);
              const ranged = inRange(c.date);
              const isToday = sameDay(c.date, today);
              return (
                <button
                  type="button"
                  key={i}
                  disabled={disabled}
                  onClick={() => pick(c.date)}
                  className={cn(
                    "flex h-8 items-center justify-center rounded-md text-xs transition-colors",
                    !c.current && "text-muted-foreground/40",
                    disabled && "cursor-not-allowed text-muted-foreground/25 line-through",
                    active && !disabled && "bg-gradient-to-br from-sky-500 to-indigo-500 font-semibold text-white shadow-[0_0_12px_-2px_rgba(99,102,241,0.8)]",
                    ranged && !disabled && "bg-accent text-foreground",
                    !active && !ranged && !disabled && "hover:bg-accent",
                    isToday && !active && !disabled && "ring-1 ring-primary/50"
                  )}
                >
                  {c.date.getDate()}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
