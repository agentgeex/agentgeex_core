/**
 * Notices / work logs list page (/notices)
 * Stored values (log/decision/…, private/project/network) are never translated — labels only.
 */
export default {
  colType: "Type",
  colTitle: "Title",
  colProject: "Project",
  colVisibility: "Visibility",
  colTime: "Time",
  untitled: "(untitled)",
  searchPlaceholder: "Search title / body / agent / project…",
  fType: "Type",
  fProject: "Project",
  empty: "No notices yet",
  type: {
    log: "Log",
    decision: "Decision",
    error: "Pitfall",
    handoff: "Handoff",
    config: "Config",
    other: "Other",
  },
  visibility: {
    private: "Private",
    project: "Project",
    network: "Network",
  },
} as const;
