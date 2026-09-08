---
name: review
description: Run the style-reviewer then comment-reaper pass over a target. Use before asking for a commit, or when someone says to review a diff, a commit or a path. Takes an optional target; defaults to uncommitted changes.
argument-hint: [<path>|<ref>|<range>]
---

# Review pass

Two agents, always in this order, and `humanize` after both if the prose still needs it. `style-reviewer` settles structure and naming; `comment-reaper` then decides which comments survive what is left. Running them the other way round writes comments for code that is about to move.

## Work out the target

The argument, if there is one, is the target. With no argument, use uncommitted changes.

| argument looks like | target |
|---|---|
| nothing | uncommitted work: `git diff HEAD` for every tracked change, staged or not, plus anything `git status --porcelain --untracked-files=all` reports as untracked |
| a ref, a range, `HEAD~3..HEAD` | that commit or range |
| a path | the files under it |

`git diff` alone shows unstaged work only, so anything already staged would be reviewed as though it did not exist. `git diff HEAD` covers both, and `--untracked-files=all` catches a new file that has no diff yet and must be read whole.

Say the target back before you start, so a wrong reading costs one line instead of a full pass.

## Check the size first

A path target on a large directory is the expensive case: the reaper spawns a sub-agent per file.

Count the files. Above roughly forty for the reviewer or thirty for the reaper, stop and propose a split into batches that follow the directory structure, then ask which to run. **Do not batch on your own initiative.** Whoever asked decides how much to take at once, and they usually want to commit between batches.

## Run them

Launch `style-reviewer` with the target named explicitly in the prompt, and any specific worry the caller mentioned. Wait for it. Relay what it changed and what it flagged.

Then launch `comment-reaper` with the same target. Wait for it. Relay its counts, the comments it kept with the questions its sub-agents failed, the facts it moved, and any code findings.

The code findings are the part worth reading twice. A comment that disagrees with the code means one of them is wrong.

## Afterwards

Both agents edit the working tree and neither commits. Say what is ready and stop there.

If either agent changed code rather than only comments, run the targeted checks named in `.claude/review-profile.md` and report what they said. Never run a full suite.
