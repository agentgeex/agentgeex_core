# AgentGeeX User Guide

AgentGeeX is a system for getting work done through several agents working together: register hosts, run agents on them, organize their work with projects and tasks, and track every result with execution records.

## How this book is organized

The book follows the **creation path**, not the internal data structures. It starts with a single machine and ends with a complex piece of work running to completion on its own.

| Chapter | Content | Question it answers |
|---|---|---|
| 1. Host | How a host is registered and what runs on it | I have a machine; how do I make it run agents |
| 2. Agent | How a role is created, deployed and migrated | Who does the work |
| 3. Project | How members and permission boundaries are organized | How do I manage a group of agents |
| 4. Task | How a capability is defined and published | How does work become an executable unit |
| 5. Execution | How one run flows and who handles it when stuck | What happens after dispatch |
| 6. Quick command | How a common operation becomes a fixed entry point | How do I avoid retyping the same instruction |
| 7. Session and assistant | Where a run lands and how parallel selves work | How do contexts stay out of each other's way |
| 8. Plan | How multi-step work is orchestrated | How does a dozen-step job run by itself |

## Core concepts

| Concept | Description | Chapter |
|---|---|---|
| Host | An Agent runtime that can receive tasks. One machine can run several hosts | 1 |
| Agent | An execution unit with an identity, a role, a project and a host | 2 |
| Project | The organizational unit and permission boundary for agents | 3 |
| Task | A capability an agent has, made of a specification and structured attributes | 4 |
| Execution | One concrete run of a task, with instruction, result, duration and status | 5 |
| Quick command | A parameterized entry point that turns a common operation into one call | 6 |
| Session | The unit a run lands in. Every execution lands in some session | 7 |
| Assistant | An agent's own parallel self: same capabilities, own session, own files | 7 |
| Plan | Several executions arranged by dependency and advanced by the watchdog | 8 |

## Basic relations

```
host       carries      Agent
project    organizes    Agent
Agent      has          task
task       produces     execution
execution  lands in     session
plan       orchestrates execution
```

No layer substitutes for another, and a missing layer means a missing capability: without a host nothing can run, without a project there is no permission boundary, without a task behavior cannot be reused, without an execution there is nothing to trace.

## Reading notes

Read the chapters in order on first use. Chapters 4 and 5 are the conceptual divide of the book; finish them before operating the system. If you already understand the concepts and only need parameters, go straight to the attribute tables and MCP method tables in each chapter.

Every chapter covers, in order: what this is, which attributes it has, how to operate it, and when not to use it. For exact column definitions, open the collapsed block in the body text; that is where the full field list lives.
