import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * ██ Descriptions — read-only description list (table style, label column on light gray)
 * ────────────────────────────────────────────────────────────────────────
 * Added 2026-10-29 (Feng asked to match vue-admin-tpl / Element Plus `Descriptions` border mode).
 *
 * Shape: a real `<table>` with four columns = label / value / label / value; a full-row field
 * `span={2}` has its value span the last three columns.
 * Why a table instead of a grid:
 *   'grid lines everywhere, adjacent cells sharing one 1px line' can only be done by `border-collapse`;
 *   grid + per-cell borders yields double lines, and patching with negative margins leaks 2px on wrap/zoom.
 *
 * ⚠️ Three hard constraints:
 *   1. **Display only**: input / select / textarea must not appear in this file (read-only means not editable);
 *   2. **The light-gray label column uses a semantic token**: `bg-table-head`
 *      (= `hsl(var(--table-head))`, token defined in index.css); do not hardcode #F5F7FA,
 *      and do not roll your own `bg-muted`.
 *
 *      ── This value must be squeezed from three sides; do not change it by feel (a real rework incident) ──
 *      A light-mode cell holds three colors at once: white frame 255 / label bg / grid line 231.7.
 *      The label bg only works when it sits **between the two**, keeping distance from both white and line:
 *        · current light `220 20% 96%` → measured rgb(243,244,247):
 *          differs from the grid line by 13.0 and from the white frame by 10.3 — both hold, so the 1px line shows.
 *        · once set to 93% → measured rgb(234,236,240): only 5.0 apart, **the grid line is swallowed by the bg**
 *          (Feng reported 'the frame is invisible' — a real incident, not an aesthetic preference).
 *        · darker breaks the white-frame side — the two differences trade off; 244-245 satisfies both best.
 *
 *      ── dark mode goes the **opposite** way; do not copy the light reasoning ──
 *      In dark, the grid line rgb(39,39,43) is brighter than the label, so the label must go
 *      **darker** for the line to surface; but the label must stay brighter than the content surface
 *      card rgb(21,21,25), otherwise the block sinks into the frame.
 *      Current dark `240 8% 12.5%` → rgb(29,29,34): 9.7 from the line, 8.3 brighter than the surface — both hold.
 *      (At 15% it is rgb(35,35,41), which is 3.3 brighter than the line — the line is swallowed again.)
 *
 *      ⚠️ **Changing this value requires verifying two differences at once:
 *        'bg vs grid line' and 'bg vs frame bg'; verifying only one always fails**
 *        (light needs >=11 and >=10; dark needs >=8 and >=8).
 *        Method: getComputedStyle for th's backgroundColor + td's borderLeftColor
 *        + ContentFrame's backgroundColor, average the three rgb values and subtract;
 *        reading the className or a screenshot is not evidence (a screenshot cannot show a swallowed 1px line).
 *   3. **Empty values are uniformly `-`** (ASCII hyphen) + `text-foreground` (normal foreground),
 *      handled by the primitive so pages do not each repeat it.
 *      Deliberately not muted: a faded dash on white reads visually as 'this cell is missing content'.
 *
 * Responsive: <768px collapses to one column (label on top, value below), via `max-md:*`
 *        turning cells into blocks; colSpan then has no effect, filler cells are hidden, no horizontal scrollbar.
 *
 * ⚠️ Invariant: **the first row must be a full row (4 cells), otherwise `table-fixed` shrinks the column count**
 *    (measured: if the first row is a span=2 full-row item, the table is fixed at 2 columns,
 *      the leftover width of later 4-cell rows is split three ways, the right-half column origin
 *      drifts from 824 to 733.8 — 90px out of line with other tables on the page).
 *    The primitive now forces 'full-row items with span>=2 always go last' (stable partitioning,
 *    keeping writing order within the same span group), so pages need no discipline to keep order.
 *    Also: **this file must not use `<colgroup>/<col>` for column widths** — `<col>` priority beats
 *    `max-md:w-full`, and the mobile label cell gets stuck as a 150px gray block.
 */
export type DescriptionSpan = 1 | 2;

export interface DescriptionsItemProps {
  /** Label text (the gray cell on the left) */
  label: React.ReactNode;
  /** Value (ReactNode); `null / undefined / '' / whitespace-only` all render as `-` */
  children?: React.ReactNode;
  /** 1 = half row (default, two fields side by side) · 2 = full row (the value spans 3 columns) */
  span?: DescriptionSpan;
}

/**
 * Placeholder component: props are only read inside `Descriptions`; it never renders itself.
 * Kept as a component for JSX readability (`<DescriptionsItem label="...">value</DescriptionsItem>`).
 */
export function DescriptionsItem(_props: DescriptionsItemProps): null {
  return null;
}
DescriptionsItem.displayName = "DescriptionsItem";

export interface DescriptionsProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Label column width (px), default 150 (same tier as the reference page) */
  labelWidth?: number;
}

// The 150px label column width is written only on th (no more <colgroup>):
// <col> sets column width with higher priority than `max-md:w-full`, and would stick the mobile label cell at a 150px gray block.
const LABEL_CELL = cn(
  "w-[150px] border border-border bg-table-head px-2.5 py-3 text-left align-middle text-sm font-semibold text-foreground",
  // <768: single-column stack, label on top; no line between it and the value below (the value's bottom border draws it)
  "max-md:block max-md:w-full max-md:border-0 max-md:border-b-0 max-md:px-3 max-md:py-2"
);

const VALUE_CELL = cn(
  "border border-border px-2.5 py-3 text-left align-middle text-sm font-normal text-foreground",
  "max-md:block max-md:w-full max-md:border-0 max-md:border-b max-md:border-border max-md:px-3"
);

/** Emptiness check: null / undefined / empty string / whitespace only */
function isBlank(node: React.ReactNode): boolean {
  if (node === null || node === undefined || node === false) return true;
  if (typeof node === "string") return node.trim() === "";
  if (typeof node === "number") return false;
  return false;
}

function renderValue(node: React.ReactNode): React.ReactNode {
  if (isBlank(node)) return <span className="text-foreground">-</span>;
  return node;
}

interface ItemEl extends React.ReactElement<DescriptionsItemProps> {}

export function Descriptions({ children, className, labelWidth = 150, ...props }: DescriptionsProps) {
  const items = React.Children.toArray(children).filter(
    (c): c is ItemEl => React.isValidElement(c) && c.type === DescriptionsItem
  );

  // A custom label column width uses inline width to override the default 150 (the default goes through className, so mobile can override it with w-full)
  const labelStyle: React.CSSProperties | undefined =
    labelWidth === 150 ? undefined : { width: labelWidth };

  // Full-row items go last (stable partitioning, no sort: strictly keep page writing order within the same span group).
  // Purpose: guarantee the first row is full → table-fixed fixes column widths from the 4 cells → multiple tables on a page align naturally.
  const halfItems = items.filter((it) => (it.props.span ?? 1) !== 2);
  const fullItems = items.filter((it) => (it.props.span ?? 1) === 2);
  const ordered = [...halfItems, ...fullItems];

  // Row packing: span=2 takes its own row; two span=1 share a row; an odd count is padded with an empty cell to close the grid
  const rows: ItemEl[][] = [];
  let bucket: ItemEl[] = [];
  for (const it of ordered) {
    if ((it.props.span ?? 1) === 2) {
      if (bucket.length) {
        rows.push(bucket);
        bucket = [];
      }
      rows.push([it]);
    } else {
      bucket.push(it);
      if (bucket.length === 2) {
        rows.push(bucket);
        bucket = [];
      }
    }
  }
  if (bucket.length) rows.push(bucket);

  return (
    <div className={cn("w-full max-md:rounded-none", className)} {...props}>
      <table className="w-full table-fixed border-collapse text-sm">
        <tbody>
          {rows.map((row, ri) => {
            const isFull = row.length === 1 && (row[0].props.span ?? 1) === 2;
            if (isFull) {
              return (
                <tr key={ri}>
                  <th scope="row" style={labelStyle} className={LABEL_CELL}>
                    {row[0].props.label}
                  </th>
                  <td colSpan={3} className={VALUE_CELL}>
                    {renderValue(row[0].props.children)}
                  </td>
                </tr>
              );
            }
            return (
              <tr key={ri}>
                {row.map((it, i) => (
                  <React.Fragment key={i}>
                    <th scope="row" style={labelStyle} className={LABEL_CELL}>
                      {it.props.label}
                    </th>
                    <td className={VALUE_CELL}>{renderValue(it.props.children)}</td>
                  </React.Fragment>
                ))}
                {row.length === 1 && (
                  <>
                    <th scope="row" aria-hidden className={cn(LABEL_CELL, "max-md:hidden")} />
                    <td aria-hidden className={cn(VALUE_CELL, "max-md:hidden")} />
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
