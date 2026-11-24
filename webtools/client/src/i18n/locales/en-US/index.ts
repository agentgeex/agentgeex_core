/**
 * English bundle — mirrors `zh-CN/` one file per namespace.
 * Rule: same key tree, same order. A missing key silently falls back to Chinese,
 * so `npm run i18n:check` compares both trees and fails on any gap.
 */
import app from "./app";
import agentProfile from "./agent-profile";
import agentDetail from "./agent-detail";
import auth from "./auth";
import common from "./common";
import header from "./header";
import nav from "./nav";
import footer from "./footer";
import tabMenu from "./tab-menu";
import page from "./page";
import profile from "./profile";
import system from "./system";
import entityDetail from "./entity-detail";
import executions from "./executions";
import status from "./status";
import sessionStatus from "./session-status";
import sessionHistory from "./session-history";
import sendDialog from "./send-dialog";
import quickCmdForm from "./quick-cmd-form";
import agentsPage from "./agents-page";
import quickCmdsPage from "./quick-cmds-page";
import hostEdit from "./host-edit";
import agentEditDialog from "./agent-edit-dialog";
import planFlow from "./plan-flow";
import mcpCheck from "./mcp-check";
import datePicker from "./date-picker";
import quickCmdDialog from "./quick-cmd-dialog";
import sessionsPage from "./sessions-page";
import plansPage from "./plans-page";
import rowActions from "./row-actions";
import noticesPage from "./notices-page";
import tasksPage from "./tasks-page";
import hostsPage from "./hosts-page";
import skillsPage from "./skills-page";
import projectsPage from "./projects-page";
import projectGroupsPage from "./project-groups-page";
import editCommon from "./edit-common";
import taskEdit from "./task-edit";
import executionEdit from "./execution-edit";
import skillEdit from "./skill-edit";
import pageDialogs from "./page-dialogs";
import dispatchExempt from "./dispatch-exempt";
import entityKind from "./entity-kind";
import detailPopup from "./detail-popup";
import nudgeDialog from "./nudge-dialog";
import dataGate from "./data-gate";
import serviceSettings from "./service-settings";
import voiceInput from "./voice-input";
import quickAccessPage from "./quick-access";
import projectEdit from "./project-edit";
import miscUi from "./misc-ui";
import miscShell from "./misc-shell";
import roles from "./roles";
import apiError from "./api-error";

export default {
  app,
  auth,
  agentProfile,
  agentDetail,
  common,
  header,
  nav,
  footer,
  ...tabMenu,
  page,
  profile,
  system,
  entityDetail,
  executions,
  sessionStatus,
  sessionHistory,
  sendDialog,
  quickCmdForm,
  agentsPage,
  quickCmdsPage,
  hostEdit,
  agentEditDialog,
  planFlow,
  mcpCheck,
  datePicker,
  quickCmdDialog,
  sessionsPage,
  plansPage,
  rowActions,
  noticesPage,
  tasksPage,
  hostsPage,
  skillsPage,
  projectsPage,
  projectGroupsPage,
  editCommon,
  taskEdit,
  executionEdit,
  skillEdit,
  pageDialogs,
  dispatchExempt,
  entityKind,
  detailPopup,
  nudgeDialog,
  dataGate,
  serviceSettings,
  voiceInput,
  quickAccessPage,
  projectEdit,
  miscUi,
  miscShell,
  roles,
  apiError,
  status,
} as const;
