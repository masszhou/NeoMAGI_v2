# Beads Working Memory Showcase

## Product Position

Beads Working Memory is a NeoMAGI showcase for turning an oversized agent task into a temporary dependency graph.

It is not long-term memory, not the durable session record, and not a replacement for NeoMAGI's Postgres-backed memory plane. It is a project-local working ledger that helps the agent remember what it is doing while a large task is still in progress.

## User Value

When a request is too large for one clean agent turn, the agent should be able to decompose it into smaller tasks, record dependencies, execute ready work in order, recover after interruption, and return only the final result plus important summary back to durable memory.

The user benefit is less context drift during long tasks:

- the agent can see which subtasks are ready;
- blocked work remains visible instead of disappearing into chat history;
- each subtask can carry a small local outcome;
- restart and resume do not require replaying the full conversation;
- durable memory receives only the compressed result, not every planning artifact.

## Experience

A user gives NeoMAGI a large goal, for example:

```text
Review this module, split the hardening work, fix the necessary issues, and report what changed.
```

The Beads Working Memory extension can then:

1. create a task graph for the goal;
2. record task dependencies and blockers;
3. select the next ready task;
4. update each task with progress, findings, or blocked status;
5. close completed tasks with concise outcomes;
6. produce a final task-graph summary for the user and optional durable memory write.

The visible product should feel like the agent gained a temporary workbench for multi-step execution, not like the user has to manage a separate issue tracker.

## Memory Boundary

Beads data is working memory. It may contain noisy decomposition, failed attempts, scratch notes, and execution details.

NeoMAGI durable memory should only receive:

- final outcome;
- key decisions;
- durable constraints discovered during the work;
- important failures or blockers that matter after the task ends;
- links or IDs needed to inspect the temporary task graph later.

Intermediate task chatter, low-value progress updates, and abandoned branches should stay in Beads or the session transcript.

## Extension Boundary

The showcase should use NeoMAGI's extension layer instead of adding task-graph behavior to core runtime.

The extension is responsible for:

- detecting whether Beads is available and initialized;
- creating and reading Beads tasks through governed tool execution;
- keeping Beads writes inside the project working-memory boundary;
- summarizing ready work without injecting the full graph into prompt context;
- exposing a small set of agent-facing tools for graph creation, next-task selection, progress updates, completion, and final summary.

NeoMAGI core remains responsible for session truth, tool policy, audit, provider execution, and durable memory writes.

## What This Is Not

- It is not a formal replacement for `design_docs/` plans or ADRs.
- It is not long-term user memory.
- It is not a team issue-tracking mandate.
- It is not a hidden permission bypass for shell, file, network, or memory writes.
- It is not a full subagent runtime; it is a task-graph workbench that can support one agent or multiple agents.

## Acceptance Shape

The showcase is useful when it can demonstrate this loop:

1. A large user request becomes a Beads dependency graph.
2. The agent executes only ready tasks and respects blockers.
3. Each completed task leaves a concise local outcome.
4. An interrupted run can resume from the graph state.
5. The final response contains the completed result and important summary.
6. Durable memory receives only the final compressed knowledge, if the user or policy allows it.

