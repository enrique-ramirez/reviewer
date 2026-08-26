# Credits

## mattpocock/skills — MIT

<https://github.com/mattpocock/skills>

Two ideas from that repository shaped this one. No files were copied; both were
adapted to run unattended against a GitHub PR rather than interactively against a
local checkout.

**The two-axis review split**, from its `code-review` skill. It separates
*Standards* ("does the code conform to this repo's documented coding standards?")
from *Spec* ("does the code faithfully implement the originating issue?"), and
keeps them apart so that one axis cannot mask failures on the other. That
separation is why `personality/90-output-contract.md` requires every finding to
declare an axis, and why a PR that is clean code but builds the wrong thing gets
flagged here at all.

Adapted rather than copied because the source of each axis differs: that skill
reads specs from local `docs/` or `specs/` directories and spawns a sub-agent per
axis, where this tool reads the PR body and linked issues from the GitHub API and
defaults to one call with two labelled sections. Per-axis calls are available via
`review.split_axes_into_separate_calls`.

**Guidance on writing for agents**, from its `writing-for-agents` skill, informs
`personality/07-voice-agent.md` and the collapsible agent block: order content by
what every branch needs versus what only some branches reach, make done and
not-done unambiguous per task, prefer vocabulary the model already has over
invented terms, and keep each meaning in exactly one place.

Its rule that *"steering by prohibition drags the forbidden behaviour into
context and makes it more available, not less"* is applied throughout
`personality/`. Guidance there is written as scope — *comment when X* — rather
than as bans. The exception is the calibration examples in
`05-severity.md`, which state plainly what sits at each severity rung; those are
reference points for a judgement call, not instructions to avoid a behaviour.

## juliusbrussee/caveman

<https://github.com/juliusbrussee/caveman>

The diff-compression tiers in `reviewer/diff.py` follow its approach to diffs —
keep file headers and changed lines, drop the rest — extended with the
`summarize_only` tier for generated files that occasionally matter.

The project itself is not used as a dependency. Its own documentation notes that
its skill shrinks output tokens while adding roughly 1–1.5k input tokens per
turn; for PR review the cost is dominated by the input diff, so the compression
is implemented directly in the script, where it is deterministic and free.
