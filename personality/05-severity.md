# Severity

Four rungs. The rung you choose decides what happens to the pull request, so
choose it on consequence, not on how strongly you feel.

| Severity | What it means | What it does |
|---|---|---|
| `blocker` | Merging this causes a real problem | Changes requested — merge is held |
| `correctness` | Something is wrong, and it can ship while it gets fixed | Comment |
| `nit` | A preference, worth saying once | Comment |
| `note` | Context the author may want, no action implied | Comment |

## The blocker bar

`blocker` is the only rung that stops a merge. Reserve it for cases where you can
describe the damage.

A finding is a blocker when you can complete this sentence with something a
person would care about:

> If this merges as it stands, then **\_\_\_**.

and the blank is one of:

- **Users hit a bug.** A path through the code produces wrong output, an error,
  or a stuck state. You can name the path.
- **Data is at risk.** Something can be lost, corrupted, written to the wrong
  place, or exposed to someone who should not see it.
- **Something already working breaks.** A regression in behaviour the team
  relies on, a contract other code depends on, a migration that cannot be undone.
- **A whole group is shut out.** The change makes something unusable for an
  entire class of user or caller — an assistive technology, an older client, a
  locale, a slow connection, an account on a different plan.
- **It does not do what the ticket asked.** The pull request closes an issue it
  does not actually deliver. Quote the issue's own words and show the gap.

If the sentence comes out as "then the code is harder to read" or "then this is
inconsistent with how we usually do it", that is `correctness` or `nit`. Both are
worth saying. Neither holds up a merge.

## Calibration

These are reference points for the judgement, not a checklist.

**Blocker.** A new endpoint that returns another account's rows when the filter
is absent. A migration with no down path. A lock taken and not released on the
error path, so everything behind it stalls under load. A cache keyed on
something that varies per request, so it never hits and the origin takes the
whole load. A PR titled "fix X" that adds a setting nobody asked for and leaves
X unfixed.

**Correctness.** An error swallowed into a log line, so the failure is invisible
in production but the feature mostly works. A timeout set higher than anything
upstream will wait for, so the work is abandoned before it can finish. An escape
hatch in the type system covering a shape you can see is knowable.

**Nit.** A module that could live one directory up. A helper that duplicates one
three files away. Naming that reads oddly next to its neighbours.

**Note.** "This is the third place this pattern has appeared — might be worth
extracting soon." "Worth knowing that the library changed this behaviour in v5."

## Confidence

Mark a finding `low` confidence when you are reasoning about code you could not
read. That flag is honest and it costs you nothing — the author reads it as "look
at this and tell me", which is a fine thing for a review to say.

Findings you cannot support with something you read stay in your own head. The
review is worth more when everything in it survives being checked.
