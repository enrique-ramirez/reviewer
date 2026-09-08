# Review profile

Copy to `.claude/review-profile.md` in the target repository and fill in every section. The review agents read this first and treat it as fact about the repository. Anything left as a placeholder makes them fall back to generic behaviour and say that they did.

Keep it short. This is the file that stops the agents from inventing a boundary the repository does not have.

## Boundary

> The one architectural rule that matters here, and how it is enforced.

Example: imports run `app` to `features` to `domain` to `shared`, enforced by the linter.
Example: `core/` knows nothing of the host application, and everything it needs arrives as a callback.
Example: no rule worth naming. Say so plainly rather than leaving this blank.

## Excluded paths

> Directories and files the agents never touch: generated code, vendored code, lockfiles, scratch.

- `node_modules/`
- `dist/`
- `*.local.md`

## Functional directives

> Suppression comments that are load-bearing here. The agents never delete or weaken one.

Example: `biome-ignore`, `eslint-disable`, `ts-expect-error`.
Example: `NOLINT`, `shellcheck disable`, `clang-format off`.

## Documentation map

> One row per documentation file, and the single job it has. The agents flag anything written in the wrong file and anything stated in two.

| file | its job |
|---|---|
| `README.md` | what this is and how to use it |
| `CONTRIBUTING.md` | how to write code here |
| `ARCHITECTURE.md` | how the pieces fit and why the boundaries sit there |

## Where an external fact belongs

> When a comment turns out to hold a fact about an outside system, the reaper moves it here rather than deleting it.

Example: `ARCHITECTURE.md`.
Example: `docs/api.md` for anything the upstream service returns.

## Commands

> Targeted forms only. The agents are forbidden from running a full suite.

| | |
|---|---|
| lint | `pnpm lint` |
| types | `pnpm typecheck` |
| one test file | `pnpm test path/to/file.spec.ts` |

## What cannot be verified here

> Code that does not build or run on a normal development machine, so a change to it is reviewed rather than tested. Write "nothing" if that does not apply.

Example: platform-specific sources that only compile on another operating system.

## Voice

> Which persona governs prose written into this repository. `house` unless there is a reason.

- Documentation and comments: `house`
- Text posted under a person's name: `house`

## Product decisions

> Who decides what this software does for a user, and what the agents do when a diff makes such a decision without them.

Example: the owner decides. An agent that finds an unasked product decision in the diff reports it as a finding and does not implement it.
