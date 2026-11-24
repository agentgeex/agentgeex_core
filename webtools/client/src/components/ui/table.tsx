import * as React from "react";
import { cn } from "@/lib/utils";

/** TableBody's zebra toggle goes to TableRow via context (so even: is emitted on tr and hover can override it) */
const TableStripedContext = React.createContext(false);

const Table = React.forwardRef<HTMLTableElement, React.HTMLAttributes<HTMLTableElement>>(
  ({ className, ...props }, ref) => (
    <div className="relative w-full overflow-auto rounded-lg border border-border">
      <table
        ref={ref}
        className={cn(
          "w-full caption-bottom text-sm",
          /* [2026-10-30 07:06 Feng] All 12px (text-xs) inside tables raised to 13px.
             Why a descendant override instead of changing each one: 12px text is scattered across table headers and
             the column renderers of a dozen pages (run_uuid, session names, task names...), changing each one
             inevitably misses some; and "small text inside a table is one single size" is a typography rule, which
             only stays stable when written on the table component.
             Scope: inside tables only; text-xs elsewhere on the page (card captions, footers) is unaffected.
             ★ Body text stays text-sm (14px); only the 12px tier changes here. */
          "[&_.text-xs]:text-[13px]",
          className,
        )}
        {...props}
      />
    </div>
  )
);
Table.displayName = "Table";

const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead ref={ref} className={cn("[&_tr]:border-b bg-muted/40", className)} {...props} />
));
TableHeader.displayName = "TableHeader";

type TableBodyProps = React.HTMLAttributes<HTMLTableSectionElement> & {
  /** zebra: light background on even rows (token bg-muted/30, adapts to dark mode; hover still overrides) */
  striped?: boolean;
};

const TableBody = React.forwardRef<HTMLTableSectionElement, TableBodyProps>(
  ({ className, striped = false, ...props }, ref) => (
    <TableStripedContext.Provider value={striped}>
      <tbody ref={ref} className={cn("[&_tr:last-child]:border-0", className)} {...props} />
    </TableStripedContext.Provider>
  )
);
TableBody.displayName = "TableBody";

const TableRow = React.forwardRef<HTMLTableRowElement, React.HTMLAttributes<HTMLTableRowElement>>(
  ({ className, ...props }, ref) => {
    const striped = React.useContext(TableStripedContext);
    return (
      <tr
        ref={ref}
        className={cn(
          "border-b transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted",
          striped && "even:bg-muted/30",
          className
        )}
        {...props}
      />
    );
  }
);
TableRow.displayName = "TableRow";

const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <th
    ref={ref}
    className={cn(
      /* [2026-10-30 07:00 Feng: changed it to 600 and "did not see it" → the real cause is "too small and too faint"]
         Measured (ink coverage on 4x zoom screenshots):
           500 + faint grey = 1.66% (as before)
           600 + faint grey = 1.86% (+12%) ← invisible to the user, that is the magnitude
           700 + faint grey = 1.89% (+14%) ← bolding alone still gives only this much
           700 + body color = 2.20% (+33%) ← this is finally visible
         Root cause: headers are 12px + muted faint grey (#71717A), body is 14px + near black (#09090B) —
         headers were already smaller and fainter than the body, bolding alone cannot turn it around.
         So: weight bold(700) + color raised to foreground/80 (#3A3A3C level, still weaker than body).
         ★ This one change applies site-wide: every table uses this TableHead, no page overrides it. */
      "h-10 border-r border-border px-3 text-left align-middle text-xs font-bold text-foreground/80 last:border-r-0 [&:has([role=checkbox])]:pr-0",
      className
    )}
    {...props}
  />
));
TableHead.displayName = "TableHead";

const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td
    ref={ref}
    className={cn("border-r border-border p-3 align-middle last:border-r-0 [&:has([role=checkbox])]:pr-0", className)}
    {...props}
  />
));
TableCell.displayName = "TableCell";

const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
  <caption ref={ref} className={cn("mt-4 text-sm text-muted-foreground", className)} {...props} />
));
TableCaption.displayName = "TableCaption";

export { Table, TableHeader, TableBody, TableHead, TableRow, TableCell, TableCaption };
