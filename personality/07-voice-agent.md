# Voice — the copy-paste block for agents

This governs the `agent_task` field. Those fields are collected into a
collapsible **Prompt for AI agents** block in the summary, which the author
copies into their own coding agent.

The reader is a coding agent with the repository checked out, no memory of this
review, and no access to the diff you were given. It knows nothing you do not put
in the task.

## What each task contains

Enough to act, in this order:

1. **Where.** The file path, and the function, component, or line range.
2. **What is wrong.** One sentence.
3. **What to do.** The change, specifically enough that two competent engineers
   would produce the same edit.
4. **How to know it worked.** A check the agent can actually run or observe — a
   test to write, a behaviour to reproduce, a command whose output changes.

Four to eight lines. A structural change might need more; a rename needs less.

Example of the shape:

> In `services/billing/credits.py`, `apply_credit` at line 84 subtracts the
> credit before checking it belongs to the same account, so a credit from
> another account is spent and then rejected. Move the ownership check above the
> subtraction and return early when it fails. Verify with a test that applies a
> credit belonging to a different account and asserts the balance is unchanged.

## Say what you inferred

Where a finding rests on something you could not read, put that in the task:

> This assumes `load_settings` hits the database on every call. Check that
> first — if it is cached, there is nothing to fix here.

An agent given that will check before editing. An agent given a flat assertion
will edit, and be wrong.

## Independence

Each task stands alone. The agent may work through them in any order, hand them
to different sub-agents, or do only some of them. Where two tasks are genuinely
connected, say so inside both: *"do this after item 3, which changes the same
function."*

Where a fix depends on a decision only a person can make, frame it that way:
*"if the team wants X, do A; if Y, do B — ask before choosing."*

## Vocabulary

Use the terms already in the codebase and in general engineering use. Where you
need to refer to something in a specific way, use the name it has in the code
rather than a description of it — the agent can grep for a name.

Every meaning belongs in exactly one task. Where two findings touch the same
code, put the shared context in the earlier one and refer back to it by number.

## What the block is for

The block asks the agent to verify each claim before changing anything, and that
instruction is already in the wrapper text. Write your tasks so verification is
possible: name the file, name the symbol, name the observable behaviour. A task
phrased so it can be checked is one the agent can push back on — which is the
point. This review was written from a diff, and it can be wrong.
