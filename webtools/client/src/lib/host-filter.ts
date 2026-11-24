/**
 * lib/host-filter.ts — the unified "host" query condition for list pages
 * ────────────────────────────────────────────────────────────────
 * [2026-10-26 Feng] "The system supports multiple hosts, add a host to the query conditions of every section"
 *
 * Association rules (data trusts a single source: `agx_agent_registry.host_id`):
 *   Agent      -> its own host_id
 *   task       -> task.agentId        -> Agent.host_id
 *   session    -> session.agentId     -> Agent.host_id
 *   execution  -> execution.agentId   -> Agent.host_id
 *   skill      -> skill.sourceAgentId -> Agent.host_id
 *   notice     -> notice.agentId      -> Agent.host_id
 *   plan       -> plan.owner          -> Agent.host_id
 *   quick cmd  -> cmd.ownerAgent      -> Agent.host_id
 *   project    -> the set of host_ids of all Agents in the project (a project can span hosts)
 *
 * ★ Why project is "multi-value": projects spanning hosts is normal (the same project has members on both
 *   test_host_mac and test_srv_a). `multi: true` makes DataTable match by "does this row's host set contain the
 *   selected host", instead of concatenating into a string and comparing equality (which never matches).
 */
import { listAgents, listHosts } from "@/providers/agentgeex";

/** Shape of the filter dropdown options (same as DataTable FilterDef.options) */
export interface HostOption {
  label: string;
  value: string;
  /** [2026-11-07 Feng] Online hosts get a green dot in front of the dropdown option; offline ones do not.
   *  Living here (rather than being written once per page) because **every host dropdown site-wide comes from
   *  this one function** -- add it once and the session/execution/plan/agent filters all take effect at the
   *  same time; new pages later need not remember it. */
  online?: boolean;
}

/** Host dropdown option: alias + IP (when the id equals the alias, show the alias once, avoiding "test_host_mac (test_host_mac)") */
export function hostOptions(): HostOption[] {
  return listHosts()
    .filter((h) => h.id)
    .map((h) => ({
      label: h.ip ? `${h.alias || h.id} · ${h.ip}` : h.alias || h.id,
      value: h.id,
      /* Only pass through the one fact "is online": offline/unknown get no marker,
         so write an explicit boolean here, not !online (false must also land as false, not undefined). */
      online: h.online === true,
    }))
    .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
}

/** agent_id -> host_id (cache a Map, avoiding a linear scan of the agents array per row) */
let agentHostMap: Map<string, string> | null = null;
let agentHostStamp = "";

function hostMap(): Map<string, string> {
  const agents = listAgents();
  // Cheap invalidation key "count + first id": rebuild when agents change after hydrate
  const stamp = `${agents.length}|${agents[0]?.id ?? ""}`;
  if (!agentHostMap || stamp !== agentHostStamp) {
    agentHostMap = new Map(agents.map((a) => [a.id, a.host_id || ""]));
    agentHostStamp = stamp;
  }
  return agentHostMap;
}

/** The host an Agent is on (unknown -> empty string) */
export function hostOfAgent(agentId?: string): string {
  if (!agentId) return "";
  return hostMap().get(agentId) || "";
}

/** Host set of a group of Agents -> comma-separated string (for DataTable's multi: true) */
export function hostsOfAgents(agentIds: (string | undefined | null)[]): string {
  const set = new Set<string>();
  for (const id of agentIds) {
    const h = hostOfAgent(id || "");
    if (h) set.add(h);
  }
  return [...set].join(",");
}

/** Host set of all Agents in a project */
export function hostsOfProject(projectId?: string): string {
  if (!projectId) return "";
  const ids = listAgents().filter((a) => a.project_id === projectId).map((a) => a.id);
  return hostsOfAgents(ids);
}

/** Common filter definition: host (the header uniformly calls it "host") */
/** Title key of the filter dropdown (this file is a pure module without hooks, the caller calls t() itself). */
export const HOST_FILTER_LABEL_KEY = "common.host";
