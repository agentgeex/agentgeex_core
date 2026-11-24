/**
 * components/agentgeex/source-dir.tsx — project source directory: copy + open in Finder
 * ────────────────────────────────────────────────────────────────
 * [2026-10-26 Feng] "The project has a path, could we make a link that opens straight into that folder"
 *
 * Why not a `file://` link:
 *   browsers forbid http(s) pages from navigating to `file://` (Chrome/Firefox block it silently, clicking does nothing) —
 *   building it would be lying to the user. So it goes through the BFF: `POST /v1/projects/{id}/actions/reveal`
 *   → invoke `open` / `explorer` / `xdg-open` on the **host running the BFF**.
 *
 * Boundaries (enforced by the backend, this only shows UI hints):
 *   - the path is never taken from the front end (source_dir in the DB is authoritative)
 *   - only allowed from the local machine by default; remote access reports an explicit error and never pretends to succeed
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { FolderOpen, Copy, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { projectsApi } from "@/providers/api";
import { cn } from "@/lib/utils";

export function SourceDirField({
  projectId,
  path,
  className,
}: {
  projectId: string;
  /** agx_project.source_dir (empty = not set) */
  path?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const [error, setError] = React.useState("");

  const value = (path || "").trim();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError(t("miscUi.sourceDir.copyFailed"));
    }
  };

  const reveal = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await projectsApi.reveal(projectId);
      toast({
        title: t("miscUi.sourceDir.openedLocally"),
        description: res.path,
        variant: "success",
      });
    } catch (e) {
      // Backend explicitly refused / environment unsupported → show it as-is, do not swallow
      setError((e as Error).message || String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!value) {
    return (
      <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        {t("miscUi.sourceDir.unset")}
        <span className="text-[11px]">{t("miscUi.sourceDir.unsetHint")}</span>
      </span>
    );
  }

  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)} data-testid="source-dir-field">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <code
          className="min-w-0 break-all rounded bg-muted/50 px-1.5 py-0.5 font-mono text-xs"
          data-testid="source-dir-path"
        >
          {value}
        </code>
        <Button
          size="sm"
          variant="outline"
          className="h-7 gap-1 px-2 text-xs"
          onClick={() => void reveal()}
          disabled={busy}
          title={t("miscUi.sourceDir.openTitle")}
          data-testid="source-dir-open"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderOpen className="h-3.5 w-3.5" />}
          {t("miscUi.sourceDir.open")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 gap-1 px-2 text-xs"
          onClick={() => void copy()}
          data-testid="source-dir-copy"
        >
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? t("miscUi.sourceDir.copied") : t("miscUi.sourceDir.copyPath")}
        </Button>
      </div>
      {error && (
        <span className="text-[11px] text-destructive" data-testid="source-dir-error">
          {error}
        </span>
      )}
    </div>
  );
}
