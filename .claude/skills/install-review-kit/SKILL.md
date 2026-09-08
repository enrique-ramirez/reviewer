---
name: install-review-kit
description: Install the review agents, prose hook and voice files into the current repository and fill in its review profile. Use when someone asks to set up the review kit, the style hook or the comment reaper in a project.
argument-hint: [copy|plugin]
---

# Install the review kit

Two parts. The files are a script; the profile is the work.

## 1. Put the files in place

Run the installer from the kit checkout:

```
<kit>/bin/install.sh <this-repo> --mode plugin
```

Use `--mode copy` when the repository must be self-contained, or when the person will work in it without the plugin installed. Copy mode duplicates the agents, hooks and voice files into `.claude/` and needs re-running to pick up kit changes.

The installer never runs git. It writes files, merges the hook into `.claude/settings.json` with a backup beside it, and prints what it did.

## 2. Fill in the profile

`.claude/review-profile.md` starts as a template of placeholders, and placeholders make both agents fall back to generic behaviour. Fill in every section by reading the repository, not by asking.

| section | where to look |
|---|---|
| Boundary | the linter config for import rules, the top-level directory names, any existing architecture document |
| Excluded paths | `.gitignore`, generated file headers, vendored directories, lockfiles |
| Functional directives | grep the tree for suppression comments and see which ones the build depends on |
| Documentation map | the markdown files at the root, and what each actually contains today |
| Where an external fact belongs | whichever document already holds facts about outside systems |
| Commands | `package.json` scripts, the Makefile, the CI workflow. Targeted forms only, never a full suite |
| What cannot be verified here | platform-specific sources, anything needing hardware or a service |

Then put the two judgement calls in front of whoever asked, rather than deciding them:

**Voice.** Which persona governs prose here. `house` for anything a stranger reads. A named persona only for text that posts under a person's name.

**Product decisions.** Who decides what the software does for a user, and what an agent does when it finds such a decision in a diff.

## 3. Local files

`.claude/style-patterns.local.txt` holds the hostnames, addresses and machine names that must not reach a commit. It is gitignored, so it never publishes what it exists to keep out. Ask for those; do not guess them from the shell history or the environment.

`.claude/style-exempt.txt` holds paths the hook skips. The contributing guide goes there, and so does any file whose subject is the rules, because it has to quote what it forbids.

## 4. Check it

Write a scratch file containing an em dash and a phrase from the register list, confirm the hook reports both, then delete it. An installed hook that does not fire is worse than none, because it reads as a guarantee.

Report what you installed, what you filled in from the code, and the two judgement calls you left open.
