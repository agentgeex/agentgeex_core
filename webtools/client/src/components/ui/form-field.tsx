import * as React from "react";
import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";

/**
 * Site-wide unified form layout primitive (AgentGeeX spec)
 * ─────────────────────────────────────────────
 * Spec (set by Feng 2026-09-29, referencing vue-admin-tpl 'components→forms→all examples'):
 *   (1) Label and input are always arranged side by side (span horizontal: label left, control right)
 *   (2) Labels are right-aligned: all label right edges in a column line up on one vertical line
 *      → labels of different lengths still align, and all input left edges match
 *   (3) Label column width fixed at 8em (≈128px, fits about 8 CJK chars, long labels do not wrap)
 *   (4) Label↔input spacing uniformly gap-3 (12px)
 *   (5) The required red star * is inlined on the LEFT of the label text (::before), so right alignment still forms a column
 *
 * Usage:
 *   // Row style (one field per row)
 *   <FormRow label="Text input" htmlFor="f-text" hint="Basic text">
 *     <Input id="f-text" />
 *   </FormRow>
 *
 *   // Multi-column grid (1/2/3 fields per row)
 *   <FormGrid cols={2}>
 *     <FormField label="Company name" htmlFor="c-name"><Input id="c-name" /></FormField>
 *     <FormField label="Company short name" htmlFor="c-code" required><Input id="c-code" /></FormField>
 *   </FormGrid>
 *
 * Narrow screens (< sm) automatically degrade to label on top, control below (same as vue-admin-tpl mobile top layout).
 */

const LABEL_COL = "8em";
/**
 * ★ A bare `1fr` equals `minmax(auto,1fr)`: the track's min width takes the **content's min-content**,
 *   so once the control itself is wide enough, the track bursts the container and covers the cell on its right.
 *   Evidence (2026-11-04): in the quick command run dialog, the '(1) sender Agent' dropdown measured 415px,
 *   while its cell was only 292px → it ate the position of '(2) sender session' on the right,
 *   and clicking the middle of the dropdown actually hit the neighbouring label (Radix options would not open at all).
 *
 * Fix = add `min-w-0` to both cells (squash the grid item's automatic minimum size to 0),
 *   **not** write `minmax(0,1fr)` — that arbitrary class contains a comma, which this Tailwind
 *   version did not generate; measured, it degraded to a single column (worse than before).
 */
const FIELD_GRID = `grid gap-3 sm:grid-cols-[${LABEL_COL}_1fr] sm:items-start`;
const FIELD_CELL = "min-w-0";

/** The label itself: right-aligned + required * on the left (::before) */
function FieldLabel({
  label, htmlFor, required, className,
}: {
  label: string; htmlFor?: string; required?: boolean; className?: string;
}) {
  return (
    <Label
      htmlFor={htmlFor}
      data-field-label
      className={cn(
        "flex items-center justify-end gap-0.5",
        required && "before:text-destructive before:content-['*']",
        className
      )}
    >
      {label}
    </Label>
  );
}

/** Row-style field: each row 'label left + control right'
 *
 * align="left" (2026-10-19 Feng): switches the label column to left alignment — for 'in-block form' cases
 *   that visually read more like form items than table-style entry (e.g. the message-send dialog). Default stays right-aligned,
 *   keeping the site-wide spec unchanged.
 * labelClassName: override the label style alone (e.g. smaller, lighter, to separate levels from block titles).
 */
export function FormRow({
  label, htmlFor, required, hint, error, children, className,
  align = "right", labelClassName, ...rest
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  hint?: string;
  error?: string;
  children: React.ReactNode;
  /** Label alignment: right (default, site-wide spec) | left (in-block form) */
  align?: "left" | "right";
  /** Append/override label styles */
  labelClassName?: string;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn(FIELD_GRID, className)} data-testid={`row-${label}`} {...rest}>
      <div
        className={cn(
          FIELD_CELL,
          "flex flex-col gap-0.5 pt-1.5 text-left",
          align === "right" && "sm:text-right",
        )}
      >
        <FieldLabel
          label={label}
          htmlFor={htmlFor}
          required={required}
          className={cn(
            align === "left" && "justify-start",
            labelClassName,
          )}
        />
        {hint ? <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p> : null}
      </div>
      <div className={cn(FIELD_CELL, "flex flex-col gap-1.5")}>
        {children}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </div>
    </div>
  );
}

/** Multi-column grid container: fields per row (cols=1/2/3/4, responsive degradation) */
export function FormGrid({
  cols = 1, gap = "md", children, className, ...rest
}: {
  cols?: 1 | 2 | 3 | 4;
  gap?: "sm" | "md" | "lg";
  children: React.ReactNode;
} & React.HTMLAttributes<HTMLDivElement>) {
  const colsCls = {
    1: "grid-cols-1",
    2: "grid-cols-1 sm:grid-cols-2",
    3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
    4: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4",
  }[cols];
  const gapCls = { sm: "gap-3", md: "gap-4", lg: "gap-6" }[gap];
  return (
    <div className={cn("grid items-start", colsCls, gapCls, className)} data-cols={cols} {...rest}>
      {children}
    </div>
  );
}

/** Grid cell: one field in the grid = 'label left + control right' */
export function FormField({
  label, htmlFor, required, hint, error, children, className, ...rest
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  hint?: string;
  error?: string;
  children: React.ReactNode;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn(FIELD_GRID, className)} data-testid={`field-${label}`} {...rest}>
      <FieldLabel label={label} htmlFor={htmlFor} required={required} className={cn(FIELD_CELL, "pt-1.5 text-sm")} />
      <div className={cn(FIELD_CELL, "flex flex-col gap-1.5")}>
        {children}
        {hint ? <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p> : null}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </div>
    </div>
  );
}

export { FieldLabel };
