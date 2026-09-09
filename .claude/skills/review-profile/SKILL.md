---
name: review-profile
description: Fill in or refresh this repository's review profile by reading the code, so the review agents know its boundary, its exclusions and its commands. Use when setting up the review kit in a project, or when the profile has gone stale.
---

# Review profile

`.claude/review-profile.md` is the only file the review agents treat as fact about this repository. Placeholders left in it make both of them fall back to generic behaviour, and they will say so in their report. This skill fills it in.

## If the files are not there yet

The kit installs with a script, not with this skill:

```
<kit>/bin/install.sh <this-repo> --mode plugin
```

`--mode copy` instead when the repository must be self-contained, or when somebody will work in it without the plugin installed. The script never runs git. It writes files, merges the hook into `.claude/settings.json` with a backup beside it, prunes anything the kit no longer ships, and prints what it did.

## Fill in the profile by reading the repository

Every section, from the code rather than from questions.

| section | where to look |
|---|---|
| Boundary | the linter config for import rules, the top-level directory names, any existing architecture document. Where there is no rule worth naming, say that rather than inventing one |
| Excluded paths | `.gitignore`, generated file headers, vendored directories, lockfiles |
| Functional directives | grep for suppression comments and work out which ones the build depends on |
| Doc comments | whether a linter or a house convention requires a docstring, or whether one is judged like any other comment |
| Documentation map | the markdown files at the root, and what each actually contains today |
| Where an external fact belongs | whichever document already holds facts about outside systems |
| Commands | `package.json` scripts, the Makefile, the CI workflow. Targeted forms only, never a full suite |
| What cannot be verified here | platform-specific sources, anything needing hardware or a live service |

A boundary you cannot find in the code is a boundary the repository does not have. Write that down; it is a more useful sentence than a plausible guess.

## Put the two judgement calls in front of the owner

These are not readable off the code, and getting them wrong is worse than asking.

**Voice.** Which persona governs prose here, both what the repository already contains and what the agents write into it. `house` unless somebody's name is on the output.

**Product decisions.** Who decides what the software does for a user, and what an agent does when it finds such a decision sitting in a diff.

## Local files

`.claude/style-patterns.local.txt` holds the hostnames, addresses and machine names that must not reach a commit. It is gitignored, so it never publishes what it exists to keep out. Ask for those; do not harvest them from the shell history or the environment.

`.claude/style-exempt.txt` holds paths the hook skips. The contributing guide goes there, and so does any file whose subject is the rules, because it has to quote what it forbids.

## Check it

Write a scratch file containing an em dash and a phrase from the register list, confirm the hook reports both, then delete it. A hook that does not fire is worse than none, because it reads as a guarantee.

Report what you filled in from the code, and the two judgement calls you left open.
