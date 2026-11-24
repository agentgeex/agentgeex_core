/** Sign-in page (/login, outside AppShell) */
export default {
  title: "Sign in to the console",
  subtitle: "Enter your account to continue",
  username: "Username",
  password: "Password",
  usernamePlaceholder: "operator",
  submit: "Sign in",
  submitting: "Signing in…",
  needBoth: "Enter a username and password",
  invalid: "Invalid credentials. Please sign in again.",
  dataFailed: "Signed in, but failed to load data: {{message}}",
  failed: "Sign-in failed. Check that the backend is running.",
  ttlHint: "Sessions last 30 days; sign in again after that",
} as const;
