/**
 * pages/agentgeex/agent-profile.tsx — agent profile (detail page)
 * ────────────────────────────────────────────────────────────────
 * [2026-10-31 Feng] "Clicking an agent tile takes you to the console page, but you also need a detail page for the agent.
 *   Put a details entry behind the OpenClaw icon; clicking in lets you see what state it is really in."
 *
 * Division of labour with `/agents/:id` (console):
 *   · `/agents/:id`         = where work happens: task templates, executions, sessions, assistants, quick commands
 *   · `/agents/:id/profile` = **what this agent itself is**: identity, ownership, where it runs,
 *     whether it is fully equipped (AGENTS.md / SOUL.md persona files), instructions, prompt, skill allowlist
 *
 * Why a separate page instead of stuffing it on top of the console:
 *   the console already has a header + exemption bar + 5 cards above; 30 more fields would drown the "work entry".
 *   And "what state is this agent really in" is a **low-frequency but must-see-at-once** question; flipping through
 *   cards does not answer it.
 *
 * Field conventions match the other 9 detail pages (Descriptions table + RichTextView for long text).
 */
import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Check, Copy, Loader2, RefreshCw, ShieldCheck, SquareTerminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Descriptions, DescriptionsItem } from "@/components/ui/descriptions";
import { RichTextView } from "@/components/ui/rich-text-view";
import { AgentAvatar } from "@/components/agentgeex/agent-avatar";
import { DetailShell, DetailSection, RawBlock } from "@/components/agentgeex/detail-shell";
import { StatusPill } from "@/components/agentgeex/data-table";
import { formatRemaining } from "@/components/agentgeex/dispatch-exempt-panel";
import { agentsApi, type AgentPathSlot, type AgentProfile, type PersonaFile } from "@/providers/api";
import { cn } from "@/lib/utils";
import { roleLabel } from "@/lib/role-label";
import { apiErrorText } from "@/lib/api-error";

/* Small-value renderer: same as other detail pages (monospace identifiers / numbers) */
function Mono({ children }: { children: React.ReactNode }) {
  if (children === null || children === undefined || children === "") return null;
  return <span className="break-all font-mono text-[13px]">{children}</span>;
}
function Num({ value }: { value: number }) {
  return <span className="tabular-nums">{value.toLocaleString("en-US")}</span>;
}

/* Key table for status/role: the actual text is in the language pack (`agentProfile.deploy.*` / `.role.*`).
    Here it only maps "which key this enum value corresponds to"; translation happens at display time. */
const DEPLOY_KEY: Record<string, string> = {
  pending: "agentProfile.deploy.pending",
  deploying: "agentProfile.deploy.deploying",
  deployed: "agentProfile.deploy.deployed",
  failed: "agentProfile.deploy.failed",
  template: "agentProfile.deploy.template",
};

/** Display key for kind (the kindLabel the BFF gives is Chinese, unusable when the UI switches to English) */
const KIND_KEY: Record<string, string> = {
  template: "agentProfile.kind.template",
  clone: "agentProfile.kind.clone",
  native: "agentProfile.kind.native",
};

/** One path + where it came from + whether it really exists.
 *
 *  Why show the "origin": agent_root_dir is entirely empty in the DB, so this path on the UI is **derived**.
 *  Not labeling it would be lying — someone would take it to the DB looking for the field and not find it.
 *  Likewise exists is only decided on the same host: claiming "exists" across hosts is a guess, and a wrong guess makes
 *  people operate on a path that does not exist. */
function PathSlot({
  slot,
  testId,
  emptyHint,
  isTemplate,
}: {
  slot?: AgentPathSlot;
  testId: string;
  emptyHint?: string;
  /** A template Agent has no runtime directory — so "not found on this machine" would be the wrong wording;
   *  it should not exist at all. Saying "not found" sends people to investigate a problem that does not exist. */
  isTemplate?: boolean;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = React.useState(false);
  const value = slot?.value ?? "";

  if (!value) {
    return (
      <span className="flex flex-wrap items-baseline gap-2" data-testid={testId} data-source="none">
        <span className="text-muted-foreground">{t("agentProfile.path.notRecorded")}</span>
        {emptyHint ? <span className="text-[11px] text-muted-foreground">（{emptyHint}）</span> : null}
      </span>
    );
  }

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* Clipboard unavailable → silent (consistent with RichTextView) */
    }
  };

  return (
    <span
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1"
      data-testid={testId}
      data-source={slot?.source || ""}
    >
      <span className="min-w-0 break-all font-mono text-xs" title={value}>
        {value}
      </span>
      {/* Origin: value in the DB / derived by convention */}
      {slot?.source === "derived" ? (
        <Badge variant="outline" className="shrink-0 text-[10px] font-normal text-muted-foreground">
          {t("agentProfile.path.derived")}
        </Badge>
      ) : null}
      {slot?.source === "db" ? (
        <Badge variant="outline" className="shrink-0 text-[10px] font-normal text-muted-foreground">
          {t("agentProfile.path.dbRecord")}
        </Badge>
      ) : null}
      {/* Truthfulness: only give a conclusion after actually testing */}
      {slot?.exists === true ? (
        <Badge
          variant="outline"
          className="shrink-0 text-[10px] font-normal text-success"
          data-testid={`${testId}-exists`}
        >
          {t("agentProfile.path.exists")}
        </Badge>
      ) : null}
      {slot?.exists === false && isTemplate ? (
        <Badge
          variant="outline"
          className="shrink-0 text-[10px] font-normal text-muted-foreground"
          title={t("agentProfile.path.templateNoRuntimeTitle")}
          data-testid={`${testId}-template`}
        >
          {t("agentProfile.path.templateNoRuntime")}
        </Badge>
      ) : null}
      {slot?.exists === false && !isTemplate ? (
        <Badge
          variant="outline"
          className="shrink-0 text-[10px] font-normal text-warning"
          title={t("agentProfile.path.notFoundTitle")}
          data-testid={`${testId}-missing`}
        >
          {t("agentProfile.path.notFound")}
        </Badge>
      ) : null}
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-6 w-6 shrink-0 text-muted-foreground hover:text-foreground"
        aria-label={t("agentProfile.path.copy")}
        data-testid={`${testId}-copy`}
        onClick={copy}
      >
        {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
      </Button>
    </span>
  );
}

export function AgentProfilePage() {
  const { t } = useTranslation();
  const { agentId = "" } = useParams();
  const navigate = useNavigate();
  const [d, setD] = React.useState<AgentProfile | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  /** Currently expanded persona file (null = show only the list, do not expand the body) */
  const [openFile, setOpenFile] = React.useState<string>("");

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setD(await agentsApi.profile(agentId));
    } catch (e) {
      const err = e as { status?: number; message?: string };
      setError(err.status === 404 ? t("agentProfile.notFoundAgent", { id: agentId }) : apiErrorText(e, t));
      setD(null);
    } finally {
      setLoading(false);
    }
  }, [agentId, t]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const exempt = d
    ? d.dispatchExemptActive
      ? { state: "active" as const, text: t("agentProfile.exempt.active", { time: formatRemaining(d.dispatchExemptRemainingSeconds, t) }) }
      : d.dispatchExemptUntil
        ? { state: "expired" as const, text: t("agentProfile.exempt.expired", { at: d.dispatchExemptUntil }) }
        : { state: "none" as const, text: t("agentProfile.exempt.none") }
    : null;

  const filled = (d?.personaFiles ?? []).filter((f) => f.filled).length;
  const total = (d?.personaFiles ?? []).length;

  return (
    <DetailShell
      backTo="/agents"
      backLabel={t("agentProfile.backToList")}
      testId="page-agent-profile"
      leading={
        <AgentAvatar
          name={d?.name ?? agentId}
          id={d?.id ?? agentId}
          avatarUrl={d?.avatarUrl}
          className="h-10 w-10"
          textClassName="text-sm"
        />
      }
      title={d?.name ?? agentId}
      subtitle={d?.id ?? agentId}
      badges={
        <>
          {d && (
            <Badge variant="outline" className="text-[11px]" title={d.role}>
              {roleLabel(d.role, t)}
            </Badge>
          )}
          {d && (
            <Badge variant="outline" className="text-[11px]" title={d.kindLabel}>
              {KIND_KEY[d.kind] ? t(KIND_KEY[d.kind]) : d.kindLabel}
            </Badge>
          )}
          {d && <StatusPill status={d.status} />}
          {loading && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> {t("agentProfile.loading")}
            </span>
          )}
        </>
      }
      actions={
        <>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" /> {t("agentProfile.refresh")}
          </Button>
          {/* The profile page and the console are entries to each other: after seeing "what it is", go on to "what it does" */}
          <Button
            size="sm"
            className="gap-1.5"
            onClick={() => navigate(`/agents/${agentId}`)}
            data-testid="profile-to-workspace"
          >
            <SquareTerminal className="h-4 w-4" /> {t("agentProfile.openConsole")}
          </Button>
        </>
      }
    >
      {error && (
        <div className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <DetailSection title={t("agentProfile.section.basic")} testId="agent-profile-basic">
        <Descriptions data-testid="agent-profile-table">
          <DescriptionsItem label={t("agentProfile.label.name")}>{d?.name}</DescriptionsItem>
          <DescriptionsItem label="Agent ID">
            <Mono>{d?.id}</Mono>
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.role")}>
            {d ? (
              <span className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline" className="font-mono text-[11px]">{d.role}</Badge>
                <span className="text-[12px] text-muted-foreground">
                  {roleLabel(d.role, t)}
                </span>
              </span>
            ) : null}
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.kind")}>
            {d ? (
              <span className="flex flex-wrap items-center gap-1.5">
                <span>{KIND_KEY[d.kind] ? t(KIND_KEY[d.kind]) : d.kindLabel}</span>
                {d.isTemplate && (
                  <Badge variant="outline" className="text-[10px] text-muted-foreground">{t("agentProfile.deploy.template")}</Badge>
                )}
              </span>
            ) : null}
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.platform")}>
            <Mono>{d?.agentType}</Mono>
          </DescriptionsItem>
          {/* [2026-11-05 Feng] Removed the whole model row: it read agx_agent_registry.base_model,
              and base_model has long been deprecated (the model follows the session, not the agent).
              Inaccurate data → showing it is misleading, so do not show it. */}
          <DescriptionsItem label={t("agentProfile.label.tags")}>
            <Mono>{d?.agentLabs}</Mono>
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.scope")}>{d?.scope}</DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.cloneFrom")} span={2}>
            <Mono>{d?.originId}</Mono>
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.intro")} span={2}>
            {d?.description}
          </DescriptionsItem>
        </Descriptions>
      </DetailSection>

      <DetailSection
        title={t("agentProfile.section.env")}
        hint={t("agentProfile.hint.env")}
        testId="agent-profile-env"
      >
        <Descriptions>
          <DescriptionsItem label={t("agentProfile.label.project")}>
            <Mono>{d?.projectName || d?.projectId}</Mono>
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.projectAdmin")}>
            <Mono>{d?.projectAdminId}</Mono>
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.host")}>
            <Mono>{d?.hostAlias || d?.hostId}</Mono>
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.isProjectAdmin")}>
            {d ? (d.isProjectAdmin ? t("agentProfile.yes") : t("agentProfile.no")) : null}
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.deployStatus")}>
            {d ? (
              <span className="flex items-center gap-1.5">
                <span>{DEPLOY_KEY[d.deployStatus] ? t(DEPLOY_KEY[d.deployStatus]) : d.deployStatus}</span>
                <Mono>{d.deployStatus}</Mono>
              </span>
            ) : null}
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.onlineStatus")}>{d ? (d.activeStatus ? t("agentProfile.online") : t("agentProfile.offline")) : null}</DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.defaultSession")} span={2}>
            <Mono>{d?.defaultSessionId}</Mono>
          </DescriptionsItem>
          {/* [2026-11-01 Feng] "You are missing something, the path of your source code".
              Reality: agx_agent_registry's agent_root_dir / agent_workspace_dir
              are **NULL for all 90 agents** (deployment info only lands on the filesystem, never written back to the DB),
              so showing the DB field = always a "—", i.e. the information does not exist.
              Now: if the DB has none, derive it from runtime conventions, mark it as "derived", and on this machine
              actually test whether it exists. */}
          <DescriptionsItem label={t("agentProfile.label.workdir")} span={2}>
            <PathSlot
              slot={d?.paths?.workspace}
              testId="path-workspace"
              isTemplate={d?.deployStatus === "template"}
            />
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.deployRoot")} span={2}>
            <PathSlot slot={d?.paths?.root} testId="path-root" isTemplate={d?.deployStatus === "template"} />
          </DescriptionsItem>
          <DescriptionsItem label={t("agentProfile.label.sourceDir")} span={2}>
            <PathSlot
              slot={d?.paths?.projectSource}
              testId="path-project-source"
              emptyHint={
                d?.projectId ? t("agentProfile.sourceDirMissing", { project: d.projectName || d.projectId }) : ""
              }
            />
          </DescriptionsItem>
        </Descriptions>
      </DetailSection>

      <DetailSection title={t("agentProfile.section.state")} hint={t("agentProfile.hint.state")} testId="agent-profile-state">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="agent-profile-counts">
          {[
            { k: "agentProfile.counts.sessions", v: d?.counts.sessions ?? 0 },
            { k: "agentProfile.counts.tasks", v: d?.counts.tasks ?? 0 },
            { k: "agentProfile.counts.executions", v: d?.counts.executions ?? 0 },
            { k: "agentProfile.counts.assistants", v: d?.counts.assistants ?? 0 },
          ].map((x) => (
            <div key={x.k} className="rounded-md border bg-muted/20 px-3 py-2 text-center">
              <div className="text-lg font-semibold leading-tight">
                <Num value={x.v} />
              </div>
              <div className="text-[11px] text-muted-foreground">{t(x.k)}</div>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted-foreground">
          <span>
            {t("agentProfile.execOk")} <span className="font-semibold text-success">{d?.counts.execOk ?? 0}</span>
            {" / "}{t("agentProfile.execFail")} <span className="font-semibold text-destructive">{d?.counts.execFail ?? 0}</span>
          </span>
          <span>
            {t("agentProfile.label.createdAt")} <Mono>{d?.createdAt}</Mono>
          </span>
          <span>
            {t("agentProfile.label.lastActive")} <Mono>{d?.lastActiveAt}</Mono>
          </span>
        </div>

        {/* Dispatch exemption: only meaningful for workers (other roles already have L5), same rule as the card/console */}
        {d?.dispatchExemptApplicable !== false && exempt && (
          <div className="mt-2 flex items-center gap-2 text-[12px]" data-testid="agent-profile-exempt">
            <span className="text-muted-foreground">{t("agentProfile.label.dispatchExempt")}</span>
            {exempt.state === "active" ? (
              <Badge variant="outline" className="gap-1 border-success/40 bg-success/10 text-success">
                <ShieldCheck className="h-3 w-3" />
                {exempt.text}
              </Badge>
            ) : exempt.state === "expired" ? (
              <Badge variant="outline" className="gap-1 border-warning/40 bg-warning/10 text-warning">
                <AlertTriangle className="h-3 w-3" />
                {exempt.text}
              </Badge>
            ) : (
              <span className="text-muted-foreground">{t("agentProfile.unset")}</span>
            )}
          </div>
        )}
      </DetailSection>

      <DetailSection
        title={t("agentProfile.section.personas")}
        hint={
          d
            ? t("agentProfile.hint.personasFilled", { filled, total })
            : t("agentProfile.hint.personas")
        }
        testId="agent-profile-persona"
      >
        {d ? (
          <>
            <div className="grid gap-1.5 sm:grid-cols-2" data-testid="persona-list">
              {d.personaFiles.map((f) => (
                <PersonaRow
                  key={f.key}
                  file={f}
                  open={openFile === f.key}
                  onClick={() => setOpenFile(openFile === f.key ? "" : f.key)}
                />
              ))}
            </div>
            {openFile &&
              (() => {
                const f = d.personaFiles.find((x) => x.key === openFile);
                if (!f) return null;
                return (
                  <div className="mt-2">
                    <RichTextView
                      key={f.key}
                      title={f.label}
                      value={f.body}
                      mode="text"
                      testId={`persona-body-${f.key}`}
                      emptyText={t("agentProfile.emptyFile")}
                    />
                  </div>
                );
              })()}
          </>
        ) : null}
      </DetailSection>

      <DetailSection
        title={t("agentProfile.section.roleDoc")}
        hint={t("agentProfile.hint.roleDoc")}
        testId="agent-profile-xdesc"
      >
        <RichTextView
          value={d?.xDesc}
          testId="agent-xdesc"
          emptyText={loading ? t("agentProfile.reading") : t("agentProfile.noRoleDoc")}
        />
      </DetailSection>

      <DetailSection
        title={t("agentProfile.section.systemPrompt")}
        hint={t("agentProfile.hint.systemPrompt")}
        testId="agent-profile-system-prompt"
      >
        <RichTextView
          value={d?.systemPrompt}
          mode="text"
          testId="agent-system-prompt"
          emptyText={loading ? t("agentProfile.reading") : t("agentProfile.noSystemPrompt")}
        />
      </DetailSection>

      <DetailSection
        title={t("agentProfile.section.skills")}
        hint={t("agentProfile.hint.skills")}
        testId="agent-profile-skills"
      >
        {d ? (
          d.skillsAllowlist.length ? (
            <div className="flex flex-wrap gap-1.5" data-testid="agent-skills-list">
              {d.skillsAllowlist.map((s) => (
                <Badge key={s} variant="outline" className="font-mono text-[11px]">
                  {s}
                </Badge>
              ))}
            </div>
          ) : (
            <span className="text-[12px] text-muted-foreground">
              {t("agentProfile.skillsInherit")}
            </span>
          )
        ) : null}
      </DetailSection>

      {d && <RawBlock data={d} label={t("agentProfile.rawData")} />}
    </DetailShell>
  );
}

/** One persona-file row: file name + char count + whether it has content; click to view the body below */
function PersonaRow({
  file,
  open,
  onClick,
}: {
  file: PersonaFile;
  open: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`persona-${file.key}`}
      data-filled={file.filled ? "1" : "0"}
      aria-expanded={open}
      className={cn(
        "flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-left text-[12px] transition-colors",
        open ? "border-primary/50 bg-primary/5" : "bg-muted/20 hover:bg-muted/40",
        !file.filled && "text-muted-foreground",
      )}
    >
      <span className="flex items-center gap-1.5">
        <span className="font-mono">{file.label}</span>
        {!file.filled && <span className="text-[10px] opacity-70">{t("agentProfile.notConfigured")}</span>}
      </span>
      <span className="tabular-nums text-[11px] text-muted-foreground">
        {file.filled ? t("agentProfile.chars", { n: file.chars.toLocaleString("en-US") }) : "—"}
      </span>
    </button>
  );
}
