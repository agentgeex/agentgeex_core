/**
 * Toast · lightweight global feedback
 * ────────────────────────────────────────────────────────────────────────────
 * Purpose: the "action feedback" primitive for page cases (saved / deleted / failed).
 *
 * Usage:
 *   <ToastProvider> wraps the app (already mounted at the App level)</ToastProvider>
 *   const { toast } = useToast();
 *   toast({ title: "Saved", description: "P-1001 · Wireless earphones", variant: "success" });
 *
 * Traits:
 *   - no third-party dependency (hand-rolled, z-index above Dialog's z-50);
 *   - auto-dismiss (duration defaults to 3000ms, 0 = never auto-close);
 *   - role="status" + aria-live, accessible;
 *   - data-testid="toast-viewport" / "toast" for automated assertions.
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, AlertCircle, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastVariant = "default" | "success" | "destructive";

export interface ToastOptions {
  title: string;
  description?: string;
  variant?: ToastVariant;
  /** Auto-close delay in milliseconds, 0 means never; defaults to 3000 */
  duration?: number;
}

interface ToastItem extends ToastOptions {
  id: string;
}

interface ToastContextValue {
  toast: (opts: ToastOptions) => string;
  dismiss: (id: string) => void;
}

const ToastContext = React.createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = React.useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}

const VARIANT_STYLE: Record<ToastVariant, string> = {
  default: "border-border bg-card text-card-foreground",
  success: "border-success/30 bg-success/10 text-foreground",
  destructive: "border-destructive/40 bg-destructive/10 text-foreground",
};

function ToastIcon({ variant }: { variant: ToastVariant }) {
  if (variant === "success") return <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />;
  if (variant === "destructive") return <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />;
  return null;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = React.useState<ToastItem[]>([]);
  const idRef = React.useRef(0);
  const timers = React.useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = React.useCallback((id: string) => {
    setItems((list) => list.filter((t) => t.id !== id));
    const t = timers.current.get(id);
    if (t) {
      clearTimeout(t);
      timers.current.delete(id);
    }
  }, []);

  const toast = React.useCallback(
    (opts: ToastOptions) => {
      const id = `toast-${++idRef.current}`;
      const duration = opts.duration ?? 3000;
      setItems((list) => [...list, { ...opts, id }]);
      if (duration > 0) {
        timers.current.set(id, setTimeout(() => dismiss(id), duration));
      }
      return id;
    },
    [dismiss]
  );

  const value = React.useMemo(() => ({ toast, dismiss }), [toast, dismiss]);
  /* The map parameter used to be called t (ToastItem) — it shadowed the translation function. Renamed to item, the translator is tr. */
  const { t: tr } = useTranslation();

  React.useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach(clearTimeout);
      map.clear();
    };
  }, []);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        data-testid="toast-viewport"
        className="pointer-events-none fixed right-4 top-4 z-toast flex w-full max-w-sm flex-col gap-2"
      >
        {items.map((item) => (
          <div
            key={item.id}
            data-testid="toast"
            role="status"
            aria-live="polite"
            className={cn(
              "pointer-events-auto flex items-start gap-2 rounded-lg border p-3 shadow-lg",
              VARIANT_STYLE[item.variant ?? "default"]
            )}
          >
            <ToastIcon variant={item.variant ?? "default"} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-snug">{item.title}</p>
              {item.description && (
                <p className="mt-0.5 text-xs leading-snug text-muted-foreground">{item.description}</p>
              )}
            </div>
            <button
              type="button"
              className="rounded-sm opacity-60 transition-opacity hover:opacity-100 focus:outline-none"
              aria-label={tr("miscShell.toast.close")}
              onClick={() => dismiss(item.id)}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
