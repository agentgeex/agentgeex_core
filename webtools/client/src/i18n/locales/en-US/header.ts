/** Top bar: global actions (theme / language / fullscreen / account). */
export default {
  fullscreen: "Fullscreen",
  exitFullscreen: "Exit fullscreen",
  switchLanguage: "Switch language",
  switchLight: "Switch to light",
  switchDark: "Switch to dark",
  theme: "Theme",
  repo: "Repository",
  help: "Help",
  profile: "Profile",
  settings: "Settings",
  logout: "Log out",
  logoutConfirm: "Sign out of the system?",
  logoutPrompt: "Are you sure you want to log out?",
  document: "Docs",
  lock: "Lock screen",
  send: "Send",
  sendHint: "Send a message / dispatch a task / run a quick command (available on any page)",
  /** The "service settings" entry and badge (upgraded from the old "test → MCP self-check") */
  serviceSettings: "Service settings",
  serviceBadge: "SVC",
  /** Confirm button of the logout dialog (the title is "Sign out", the button must say "confirm signing out") */
  logoutConfirmBtn: "Log out",
} as const;
