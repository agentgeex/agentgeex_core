import * as React from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/**
 * EmptyState — the unified empty-state component
 *
 * Design constraints (aligned with baseline-ui):
 *  - Every empty state must have a 'clear next step' (an action, or guidance in the description); bare 'no data' copy is banned.
 *  - Icons come from lucide, with converged sizes (regular size-6 / compact size-4).
 *  - compact is for dropdowns, overlays and small components; regular is for full pages / tables / card areas.
 */
export interface EmptyStateProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  /** Icon: a lucide component (recommended) or any ReactNode */
  icon?: React.ComponentType<{ className?: string }> | React.ReactNode;
  /** Main copy */
  title?: React.ReactNode;
  /** Description / next-step guidance */
  description?: React.ReactNode;
  /** A clear action entry (button etc.) */
  action?: React.ReactNode;
  /** Compact mode: for dropdowns / overlays / small containers */
  compact?: boolean;
}

const EmptyState = React.forwardRef<HTMLDivElement, EmptyStateProps>(
  (
    { icon, title, description, action, compact = false, className, children, ...props },
    ref
  ) => {
    const { t } = useTranslation();
    const renderedIcon = React.isValidElement(icon)
      ? icon
      : icon
        ? React.createElement(icon as React.ComponentType<{ className?: string }>, {
            className: cn(compact ? "h-4 w-4" : "h-6 w-6", "text-muted-foreground"),
          })
        : null;

    return (
      <div
        ref={ref}
        className={cn(
          "flex flex-col items-center justify-center text-center",
          compact ? "gap-1 py-6" : "gap-1 py-10",
          className
        )}
        {...props}
      >
        {renderedIcon ? (
          <div
            className={cn(
              "mb-2 flex items-center justify-center rounded-full bg-muted",
              compact ? "h-8 w-8" : "h-12 w-12"
            )}
          >
            {renderedIcon}
          </div>
        ) : null}
        <p className={cn("font-medium", compact ? "text-xs" : "text-sm")}>
          {title ?? t("miscShell.noData")}
        </p>
        {description ? (
          <p className="mt-0.5 max-w-sm text-xs text-muted-foreground text-pretty">{description}</p>
        ) : null}
        {action ? <div className={cn(compact ? "mt-2" : "mt-4")}>{action}</div> : null}
        {children}
      </div>
    );
  }
);
EmptyState.displayName = "EmptyState";

export { EmptyState };
