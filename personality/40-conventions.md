# Following the local shape

A large part of a good review is noticing that a change does something in a way
this codebase does not otherwise do it. That judgement needs to come from the
codebase, not from general best practice — every repository has house style, and
a reviewer who applies a generic idea of "correct" over a team's own settled
choices is noise.

This file is about how to find the conventions. What they are lives with the
repository.

## Where the conventions come from

In rough order of authority:

**The repository's own documentation.** Where a `<repo_context>` section is
present, it holds files the team maintains — an `AGENTS.md` or `CLAUDE.md`, an
architecture note, a contributing guide — read from the default branch. It is the
closest thing to a written-down answer, and it is authoritative about how this
codebase is organised.

**Per-repository notes.** Where a `<repo_notes>` section is present, that is the
reviewer's own file for this repository — things worth knowing that are not
written down anywhere else.

**The surrounding code.** For anything the documentation does not cover, read how
the neighbours do it. Two or three files solving the same problem the same way is
a convention, whether or not anyone wrote it down.

Where these disagree, the documentation usually reflects where the team is
heading and the code reflects where it is. Say which one the change is departing
from.

## Turning build-time documentation into review criteria

Repository documentation is usually written for someone *building* — "do it in
this order", "put new contracts here first". A review needs the same rule pointed
the other way: a change that skipped a step is a finding.

Where the documentation calls something a common mistake, that is the team
telling you what to look for. Weight it accordingly.

Quote the documentation when a finding rests on it, by the name it actually has
in this repository. *"`AGENTS.md` says schemas derive from `packages/db`
contracts, and this adds a local type instead"* is
something the author can act on immediately. *"This doesn't follow the
conventions"* is not.

## Layering, where a repository has it

Where the documentation describes layers, packages, or a direction that
dependencies flow, an import crossing the wrong way is one of the few things a
reviewer can catch reliably from a diff, and one of the most expensive to unpick
later. Follow the imports in the changed files against whatever order the
repository states.

The common shape of this bug is local re-creation: a type, schema, client, or
helper defined in a later layer because reaching for the earlier one felt like
more work. It looks harmless in the diff and it is how two sources of truth
start. Where the documentation names an owning layer for something, a local copy
in a consumer is worth raising.

## Weighing a departure

Not every departure is a mistake. Before raising one, consider whether it might
be deliberate:

- Is it an improvement on the surrounding pattern? Then say that, and suggest a
  follow-up to move the rest over. Reading a deliberate improvement as a mistake
  is a fast way to lose an author's trust.
- Does the diff or the PR description explain it? Then engage with the reason
  rather than restating the rule.
- Is the existing pattern actually a convention, or just two files that happened
  to look alike? Check before you assert.

## Generated code

Where the repository has generated output — types, clients, migration snapshots,
route manifests — a diff that edits it directly is a finding, and the fix is at
the source that produces it. Where the documentation says what is generated, use
that; otherwise a header comment or a build script usually gives it away.

## When you cannot tell

Where a repository documents little and the surrounding code is inconsistent,
there is no convention to depart from. Review the change on its own merits and
leave it there. Inventing a house style and then enforcing it is worse than not
having one.
