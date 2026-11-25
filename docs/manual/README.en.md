# AgentGeeX User Guide (English)

> Status: English translation of the Chinese draft, 2026-11-25. One introduction plus eight chapters, pending review chapter by chapter.
> Chinese original: [`README.md`](./README.md), chapters under [`zh-CN/`](./zh-CN/).

## How this book is organized

The book follows the **creation path**, not the internal data structures. It starts with a single machine and ends with a complex piece of work running to completion on its own.

## Contents

| Chapter | File | Content |
|---|---|---|
| Introduction | [`en-US/00-intro.md`](./en-US/00-intro.md) | Structure of the book, core concepts, basic relations |
| 1. Host | [`en-US/01-host.md`](./en-US/01-host.md) | Host records, platform configuration, processes, access |
| 2. Agent | [`en-US/02-agent.md`](./en-US/02-agent.md) | Composition, roles and permissions, identity files, creation and deployment, migration |
| 3. Project | [`en-US/03-project.md`](./en-US/03-project.md) | Organization and permission boundaries, creation flow, relation to hosts |
| 4. Task | [`en-US/04-task.md`](./en-US/04-task.md) | Definition, parameters, MCP operations, built-in tasks, specification |
| 5. Execution | [`en-US/05-execution.md`](./en-US/05-execution.md) | State transitions, timeouts, watchdog, receipts and acknowledgement |
| 6. Quick command | [`en-US/06-quick-cmd.md`](./en-US/06-quick-cmd.md) | Two modes, private and public, templates and parameters, landing point |
| 7. Session and assistant | [`en-US/07-session-assistant.md`](./en-US/07-session-assistant.md) | Session types, assistant mechanism, file layout, session-level models |
| 8. Plan | [`en-US/08-plan.md`](./en-US/08-plan.md) | Three topologies, three actions, activation modes, completion notice |

## Writing conventions

1. **Declarative sentences.** State what the thing is, how to use it, how to fill in the parameters, and why it works that way. No rhetorical questions, no lead-ins, no self-test at the end.
2. **The body explains concepts and criteria; it does not dump SQL by default.** Anything that must be exact to the column goes into a `<details>` block.
3. **Every chapter ends with the cases where it does not apply.** Examples must be runnable and numbers must have been checked.
4. **Tables use the form "Name | Description"**, with a technical description in the second column and no empty header cell.
5. **Wherever structure is described, the operation entry point is given in the same section.** For any table, column or insertion point, state that day-to-day work goes through MCP methods and needs no hand-written payload.
