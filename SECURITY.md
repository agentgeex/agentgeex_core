# Security Policy

**English** · [简体中文](SECURITY.zh-CN.md)

AgentGeeX is self-hosted software that runs with database credentials, agent
identities, and network access on your machine. We take reports seriously and we
would rather hear about a problem early than read about it later.

---

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting.**

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub Security Advisory).
3. Describe the issue and, if you can, include a minimal reproduction.

Direct link: `https://github.com/agentgeex/agentgeex/security/advisories/new`

**Please do not open a public issue, pull request, or discussion for a security
problem.** A public issue is visible to everyone including people who would
exploit it, and it cannot be un-published.

There is no dedicated security mailbox. GitHub Security Advisory is the only
supported private channel, and it is sufficient — it gives us a private thread,
lets us prepare a fix in a private fork, and produces a CVE if one is warranted.

### What to put in the report

- Affected version or commit
- Deployment shape: single host or multi-host, which harness (OpenClaw, DeepSeek
  Harness, Codex, Claude Code, custom), whether the MCP endpoint is reachable
  from outside `localhost`
- Steps to reproduce, or a proof-of-concept
- Impact as you understand it — what an attacker gains
- Whether the issue is already public anywhere

**Do not paste real credentials, tokens, or customer data into the report.**
Redact them. If we need the exact value to reproduce, we will ask for a
sanitized variant.

---

## Supported versions

| Version | Supported |
|---|---|
| `v0.1.x` | ✅ Yes — fixes are issued on the latest `v0.1.x` patch release |
| Earlier / unreleased snapshots | ❌ No |

This project has not reached 1.0. Until it does, security fixes land on the
latest patch of the current minor version; we do not maintain parallel fix
branches for older minors. If you are running an older snapshot, the fix path is
to upgrade.

---

## What we consider a vulnerability

In scope:

- **Authentication or authorization bypass.** Any way to call an MCP tool, read
  an entity, or act as an agent without the permission level that operation
  requires. Role escalation (for example, a worker obtaining
  `project_manager` capabilities) counts.
- **Tenant / project isolation break.** Reading or modifying another project's
  data when you are only entitled to your own.
- **SQL injection** through any MCP parameter, task instruction, or template.
- **Remote code execution** through the MCP server, daemon, installer, or a task
  template.
- **Credential exposure** — a path where stored secrets (database passwords,
  harness tokens, API keys) can be read by someone who should not have them,
  including via logs, API responses, or error messages.
- **Arbitrary file read/write** outside the workspace directory.
- **Denial of service that is cheap for the attacker and persistent for the
  operator** (for example, a single request that permanently wedges the
  watchdog).

Out of scope:

- **Deliberately exposing the MCP endpoint to the internet** without
  authentication in front of it. The kernel is designed to bind to loopback by
  default. Publishing `31888`/`31890` to the world is a deployment choice, not a
  vulnerability — but tell us if the defaults make it easy to do by accident.
- **Running the stack as root** or granting it broader filesystem access than it
  needs.
- **An operator with shell access on the host** reading their own configuration
  files, database, or logs. That is working as designed.
- **Prompt injection that only affects the operator's own agent.** Agents are
  instructed by design; a malicious instruction that you typed is not a
  vulnerability. Prompt injection that crosses a trust boundary (one project's
  content influencing another project's agent, or a remote page causing a
  privileged action) **is** in scope — report it.
- Reports produced solely by an automated scanner with no demonstrated impact.

If you are unsure which side something falls on, report it. We would rather
close a non-issue than miss a real one.

---

## What happens after you report

- We aim to **acknowledge within 5 business days**, and to give you an initial
  assessment (in scope / not in scope / need more information) within
  **10 business days**. These are best-effort targets, not a contractual SLA —
  this is a small maintainer team.
- We will keep you updated at reasonable intervals while we work on a fix, and we
  will tell you if we conclude it is not a vulnerability, with the reasoning.
- We will coordinate disclosure with you. Our default is to publish an advisory
  once a fixed release is available, crediting you unless you prefer otherwise.
- If you need a disclosure deadline for your own compliance reasons, say so in
  the report and we will work to it or tell you honestly if we cannot.

---

## Safe harbor

We will not pursue legal action against researchers who:

- act in good faith,
- test only against their own deployment or an explicitly authorized one,
- avoid privacy violations and data destruction,
- give us reasonable time to fix the issue before public disclosure.

---

## Hardening checklist for operators

Not vulnerabilities, but the things that actually get people compromised:

- Bind the MCP server and gateway to `127.0.0.1`, or put them behind an
  authenticated reverse proxy. Do not expose them directly.
- Rotate the JWT signing secret and database password before putting an instance
  anywhere reachable.
- Run the daemon as a dedicated, unprivileged user.
- Keep `agx_config.json` and any `*.env` files out of version control and out of
  world-readable locations.
- Upgrade to the latest patch release before reporting an issue — it may already
  be fixed.
