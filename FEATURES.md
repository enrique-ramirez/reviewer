# Features

Everything the [README](README.md) skipped over. Start anywhere.

- [The dashboard](#the-dashboard)
- [More than one repository](#more-than-one-repository)
- [Filling in history](#filling-in-history)
- [When it reviews, and when it does not](#when-it-reviews-and-when-it-does-not)
- [Review outcomes](#review-outcomes)
- [Two voices, one call](#two-voices-one-call)
- [Your review personality](#your-review-personality)
- [What the model can and cannot do](#what-the-model-can-and-cannot-do)
- [Token scoping](#token-scoping)
- [Configuration](#configuration)
- [Command line](#command-line)
- [Roadmap](#roadmap)
- [Working on it](#working-on-it)

## The dashboard

Three tabs, and the underlined letter in each name is its key — `d`, `s`, `h` —
working from anywhere.

Everything fits one screen at any terminal size: the tables shrink to the space
available rather than the window growing past the bottom. `l` hides the log pane
and gives its ten lines back to whichever table you are on, which is worth
knowing on a short terminal. It keeps recording while hidden, so bringing it back
shows what you missed.

### Dashboard — what is open right now

Every open pull request, sorted so the ones wanting a human are at the top, with
a detail pane beside it and the log underneath.

The **Open** column is how long the pull request has been sitting there — not how
long since anyone looked at it. It turns yellow after a week and red after a
month, so a change nobody is landing is visible without reading the number.

A pull request being worked on shows a spinner and a running clock: `⠹ reviewing
4m` on the board, `⠹ reviewing right now — 4m 12s` in the detail pane, and the
elapsed time beside the phase on the Pac-Man line. A review is minutes of work,
and until it finishes the row would otherwise show the previous pass's verdict as
though it were current.

Your own pull requests get statuses of their own. The reviewer skips them, so
what *it* did is never the interesting part; what matters is what everyone else
has done:

| status | meaning |
| --- | --- |
| `needs sign-off` | someone reviewed it without approving — including the case where the reviewer found it clean and left the approval to a human |
| `needs 1 more` | approved, but branch protection wants another approving review |
| `changes req.` | a reviewer asked for changes |
| `awaiting review` | a reviewer was requested and has not looked yet |
| `no reviewer` | nobody has been asked |

The detail pane names who reviewed and what they said.

### Colour means one thing

The board answers "what wants me" before you read a word of it:

| colour | means |
| --- | --- |
| red | something is wrong or blocked — deal with it |
| yellow | waiting on you specifically, and on nobody else |
| green | settled and good; merge it when you like |
| cyan | done, nothing outstanding from anyone |
| grey | nothing has happened yet |
| magenta | happening right now |

So `reviewed` is cyan — we left comments and the ball is with the author — while
`needs sign-off` is yellow, because that one is waiting on a human. The
distinction that matters most is exactly that one: "we reviewed it and nothing is
outstanding" and "somebody has to sign this off" are opposite answers to "does
this want me", and sharing a colour made the board lie.

### Summary — what landed while you were watching

This run, and only this run. When a pull request the reviewer looked at leaves
the open list having been merged, it moves here with a sentence on *what actually
changed*.

Each entry carries the size, the author, who merged it, how long it stayed open,
and how many comments we left before it landed. Press `o` to open it on GitHub.

This tab is close to free. Only pull requests we reviewed are looked up, so a
merge nobody asked us about costs nothing; the summary is one small call with no
tools and no checkout, written once and kept; and where the reviewer already
described the change during its review, that description is reused and no diff is
fetched at all.

### History — everything on record

The same record without the time limit, across every repository you watch.
Filter by author with `/` — which answers "what has this person shipped since
March" — narrow the date range with `t`, page with `[` and `]`, and clear the lot
with `Escape`.

It lives in the same SQLite database as the rest of the state, under
`~/.local/state/pr-reviewer/`, and outlives the pull requests it describes.

### Keys

The footer follows the tab you are on, so it only ever offers keys that do
something where you are.

| key | tab | does |
| --- | --- | --- |
| `d` / `s` / `h` | any | Dashboard, Summary, History |
| `o` / `Enter` | any | open the selected pull request in your browser |
| `j` / `k` | any | move |
| `l` | any | hide/show the log pane |
| `r` | any | refresh now |
| `q` | any | quit — it asks first, and says what it is about to throw away |
| `a` | Dashboard | show only what needs you |
| `/` | History | filter by author |
| `t` | History | cycle date range |
| `[` `]` | History | page |
| `b` | History | fill in past history (press again to stop) |
| `Escape` | History | clear the filters |
| `e` | any | focus the repository sidebar (only with 2+ repos) |
| `E` | any | fold the sidebar to a rail, and back |

Quitting — or `Ctrl-C`, which does not stop to ask — kills any model call in
flight rather than letting it finish. A review whose result nobody is left to
post is worth nothing, so finishing it would buy the bill and no review. Nothing
part-done is posted or recorded: the pull request is left exactly as it was, and
the next run picks it up from the start.

## More than one repository

Watching two or more adds a sidebar down the left listing them. Each entry
carries its owner and how it is doing — how many pull requests are open, how many
this tool has reviewed, and how many want a human — plus a spinner against
whichever is being reviewed right now.

Those counts cover every repository you watch, not just the one in view, so the
sidebar is where you notice something waiting somewhere you are not looking.
`All repositories` sits at the top, marked `◆` against the `▪` of a single
repository, and totals the rest.

```
▌◆ All repositories
   2 repositories
   6 open  3 reviewed  1 need you

 ⠋ platform
   acme
   5 open  3 reviewed  1 need you

 ▪ widgets
   acme
   1 open
```

Click a row to switch to it, or press `e` to hand the arrow keys to the sidebar
and `Escape` to give them back. The choice scopes all three tabs at once, so
switching repository means the same thing wherever you are.

`E` folds it down to a rail rather than hiding it — one Pac-Man ghost per
repository, still yellow if something there wants you, and still clickable. A
sidebar that disappeared would take the fact that there *are* other repositories
with it. With a single repository configured it never appears, and neither key is
offered.

Quota is shared across repositories, and they are walked in filename order, so
prefix the files with `10-`, `20-` if you want to control which gets first call.

## Filling in history

History starts empty and fills as things merge. To reach back before this tool
existed, press **`b`** on the History tab. It asks how far back, then reports what
that turned out to cost before fetching any of it. Pressing `b` again while it
runs stops it, keeping everything already fetched.

The same thing from the command line:

```sh
./run.sh --backfill            # asks how far back
./run.sh --backfill month      # or name a range up front
```

Ranges are `yesterday`, `week`, `month`, `quarter`, `year`, `all`.

```
  acme/platform: 1,594 merged pull request(s) over everything, about 16
  request(s), no model calls

  That is 1,594 pull requests — a large sweep. It makes no model calls,
  so the cost is time and API quota rather than tokens…

  Go ahead? [y/N]
```

Two things worth knowing. It records **everything merged**, not only what this
tool reviewed — history that only covered our own reviews would answer almost
nothing about a repository that predates it. And it makes **no model calls**:
backfilled entries show the pull request's own title rather than a written
summary, which is what keeps a whole-history sweep essentially free. Anything
merging from now on still gets a proper summary.

It commits as it goes, so `Ctrl-C` keeps everything already fetched, and running
it again skips what is already on record. Each repository is tracked separately.

## When it reviews, and when it does not

Every tick, for each repository:

1. Lists open PRs with a conditional request. Nothing changed → `304`, no work.
2. Applies your gates.
3. Checks state. Same head SHA as the last review and no new comments → skip.
   This is the step that keeps quota spend near zero on a quiet repo.
4. Fetches the diff, compresses it, and gathers the PR body, linked issues, and
   existing review threads.
5. Calls the model **once**.
6. Validates every proposed comment line against the real diff hunks, then posts.
7. Resolves threads that no longer apply, and replies to threads the author has
   pushed back on — after checking the pushback against the code.
8. Notices anything it reviewed that has since merged, and records what it was.

The gates, all per repository and all switchable:

| gate | default | skips when |
| --- | --- | --- |
| `skip_drafts` | on | the PR is a draft |
| `skip_own_prs` | on | you wrote it |
| `skip_if_approved_by_others` | on | someone already approved — unless a re-review was requested from you |
| `require_ci_green` | on | CI is failing or still running |
| `blocking_labels` | — | any of these labels is present |
| `required_labels` | — | none of these labels is present |
| `only_if_review_requested` | off | you are not a requested reviewer |
| `base_branches` | — | the PR targets a branch outside the list |

### What gets sent

The diff is compressed in three tiers before it goes anywhere, and everything
dropped or shortened is named in the summary comment, so a reviewed PR always
says what was not read:

- **`exclude`** — lockfiles, snapshots, build output, images. The model is told
  the file changed and by how many lines, and nothing else.
- **`summarize_only`** — migrations and `.sql` by default. Path, line counts and
  hunk headers only, so it can still say "this migration changed, worth a human
  glance" without reading 9,000 generated lines.
- **`max_file_lines` / `max_total_lines`** — a single long patch is sent
  head-and-tail with an elision marker; over the total budget, files are dropped
  lowest-risk first.

The total is measured *after* the first two tiers, so a PR that is +10,000 lines
of snapshot and +900 lines of real code counts as 900.

## Review outcomes

| Findings | Review submitted | Merge button |
|---|---|---|
| A blocker | `REQUEST_CHANGES` | blocked |
| Nits or correctness only | `COMMENT`, with an offer to approve as-is if you would rather not take them | still needs an approval |
| Nothing | `APPROVE` | unblocked |

Resolving a conversation does not by itself unblock merging — a
`REQUEST_CHANGES` review blocks until the same reviewer submits a new one. The
unblock always comes from a later `APPROVE`.

A PR with unaddressed nits and a silent author waits indefinitely, on purpose.

When a thread goes back and forth past
`review.max_disagreement_rounds_per_thread`, that one thread goes quiet and you
get a notification. Review rounds themselves are uncapped — a PR can go fifteen
rounds — and every other thread carries on.

## Two voices, one call

Each finding carries both a `human` rendering and an `agent_task` rendering. The
inline comments use `human` — plain language, aimed at a person, with a concrete
"if a user does X, then Y happens" wherever a failure can be described that way.
The collapsible **Prompt for AI agents** block uses `agent_task` — imperative and
specific, and it asks the agent to verify each claim against the code rather than
apply the changes on faith.

Both come from a single model call, so the agent block can never describe a fix
the human comment did not ask for. `personality/06-voice-human.md` and
`07-voice-agent.md` govern them separately.

## Your review personality

`personality/*.md`, concatenated in the order a repo config lists them, become
the system prompt. They are yours, they are portable, and they are the part worth
spending time on.

Five are the general reviewer and assume nothing about the stack:

| file | what it settles |
| --- | --- |
| `00-core` | who the review comes from, and what a review is for |
| `05-severity` | the four rungs, and what puts something on each |
| `06-voice-human` | how the comments people read sound |
| `07-voice-agent` | how the copy-paste agent block is written |
| `40-conventions` | how to find a codebase's house style before calling something a departure |

Three more are opt-in specialisms, added per repository in
`review.personality`: `10-frontend`, `20-accessibility`, `30-shareability`. Write
your own alongside them — any `personality/<name>.md` can be listed.

`00-core.md` is the one to read first. Its opening line sets whose review this
is, and everything else follows from it.

### Where conventions live

Three layers, in order of how specific they are:

1. **`personality/`** — how you review anything. Portable; it comes with you.
2. **The repository's own docs** — `CLAUDE.md` and whatever else
   `repo_context.paths` matches, read from the default branch. This is where a
   codebase's layering, package boundaries and "the most common mistake is X"
   should live, because the team maintains it and it stays current without anyone
   remembering to update a second copy. The reviewer treats departures from it as
   findings and quotes it when raising one.
3. **`config/repos/<name>.md`** — optional notes for one repository, sitting next
   to its JSON config. Picked up automatically; no config key. For things that
   belong to you rather than the team.

Repository docs are read from the **default branch**, not the PR branch —
otherwise a pull request could ship instructions to its own reviewer, which
matters because this tool can approve. A PR that modifies those files is surfaced
as a finding.

They are authoritative about *how the codebase is organised*, and explicitly not
about *how to review it*: severity, scope and whether to approve are settled by
`personality/` and do not move based on anything read out of the repo. That
boundary is what makes it safe to read a file the team can edit.

## What the model can and cannot do

The model is called as a pure function: compressed diff and PR context in,
structured JSON out. It gets `Read`, `Glob` and `Grep` against a clean checkout
of the PR head, so it can look at surrounding code to judge whether a change
follows the repo's conventions. It has no `Bash`, no `Write`, no `Edit`, no
network, and no GitHub token.

Everything that touches GitHub is done by the script, from JSON the script
validated first — including checking that every comment line actually exists in
the diff before posting.

### Your working copy

The reviewer uses your everyday clone for object storage and nothing else. It
fetches PR heads into a private ref namespace:

```
git fetch origin +refs/pull/<n>/head:refs/reviewer/pr-<n>
```

`refs/heads/*`, `refs/remotes/*`, your index, your working tree and your stash
are never written. The worktree is detached, lives in `$TMPDIR`, and is removed in
a `finally`; stale ones are pruned at startup. Because it is a clean tree at the
PR's head SHA, your uncommitted work and local `.env` files are not visible to the
model either.

Set `local_path` to `null` to skip all of this and review the diff alone.

## Token scoping

Create a **fine-grained** token at
<https://github.com/settings/personal-access-tokens>:

| | |
|---|---|
| Repository access | Only the repos you configure |
| Contents | **Read-only** |
| Pull requests | **Read and write** |
| Checks | **Read-only** |
| Commit statuses | **Read-only** |
| Metadata | Read-only (mandatory) |

`Checks` and `Commit statuses` are what let it see whether CI is green. Without
them GitHub refuses the `statusCheckRollup` GraphQL field, and every PR is skipped
with a message saying so — deliberately, since a reviewer that cannot see CI would
otherwise approve pull requests with failing builds.

`Contents: Read-only` is the load-bearing part: with it, this tool cannot push a
commit, create a branch, or merge anything, whatever else goes wrong.

`Pull requests: Read and write` is the minimum that allows posting reviews,
replying and resolving conversations. It also allows submitting an **approving**
review, which satisfies branch protection. That is deliberate — see
`approval.mode` — but it is real authority.

The token is never placed in the model's environment.

`./run.sh --check` probes each of these one at a time and names the exact
permission behind any failure, along with the account the token belongs to and
whether the `claude` CLI is reachable. Run it whenever the token changes.

## Configuration

| Path | Tracked? | What it is |
|---|---|---|
| `personality/*.md` | yes | Your review voice and standards. Portable across jobs. |
| `config/repos.sample/` | yes | Sample repo configs |
| `config/global.sample.json` | yes | Sample global config |
| `.env` | **no** | Your token |
| `config/global.json` | **no** | Your global config |
| `config/repos/*.json` | **no** | Your repo configs |
| `config/repos/*.md` | **no** | Optional per-repo notes |

No organisation name, username or filesystem path appears in any tracked file.

Only two values are actually required: `GITHUB_TOKEN` in `.env` and `repo` in the
repo config. Everything else has a default — including your GitHub username,
which is read from the token rather than typed twice. Set `identity` explicitly
only if you want it checked; a value that disagrees with the token is an error
rather than something the reviewer works around.

Every key is documented in the sample files themselves, in `$comment` fields that
are stripped on load, so the file you edit explains itself and there is no second
copy to drift.

State and logs go to `~/.local/state/pr-reviewer/`, deliberately outside the
checkout — they hold PR titles and, under `--debug`, diff content. Logs record
repo, PR number, decision and token counts; diff and comment bodies are written
only when you pass `--debug`.

## Command line

```
./run.sh [options]

  --init              Write the config files by asking a few questions, then
                      exit. Never overwrites; safe to re-run.
  --check             Probe what the token can reach, check the account and the
                      claude CLI, name any missing permission, and exit.
  --once              One pass, then exit
  --dry-run           Do everything except write to GitHub; the drafted review
                      is saved to the state directory so you can read it
  --backfill [RANGE]  Fill in merge history that predates this tool, then exit
  --yes               Skip the backfill confirmation (for scripts)
  --repo OWNER/NAME   Restrict to one configured repo
  --pr N              Restrict to one PR (implies --once; needs --repo)
  --force             Review even when the gates say skip (testing)
  --lean              Plain scrolling log, no dashboard, no dependencies
  --tui               Dashboard + reviewer in one window (the default)
  --debug             Write full prompts and responses to the state dir
  --state-dir PATH    Override ~/.local/state/pr-reviewer
  --config-dir PATH   Override ./config
```

Arguments that name a specific job — `--init`, `--check`, `--once`, `--backfill`
— run it and exit, so they print plainly and install nothing. Modifiers like
`--debug`, `--dry-run` and `--repo` keep the dashboard, since they still mean the
watch loop. A pipe or a cron job gets the plain log automatically; there is no
terminal to draw a dashboard on.

A dry run writes to a separate database, so repeating one is free and the live
state is untouched. A PID lockfile means a tick arriving while the previous one
is still working is skipped rather than queued, so a slow review cannot pile up.

## Roadmap

- **More than one model provider.** Everything model-shaped already goes through
  one module and one config block (`claude.command`, `claude.model`,
  `claude.extra_args`), so a second provider is a matter of another adapter
  behind the same interface rather than a rewrite.
- **CI**, once the tests can run on a hosted runner.
- **Richer per-repository health** in the sidebar — review latency, how long
  things sit waiting on a human.

## Working on it

```
reviewer/
  __main__.py   the loop, the lockfile, the walk across repositories
  bootstrap.py  --init
  identity.py   who the token belongs to
  preflight.py  --check
  pipeline.py   one repository's tick: gates, diff, model call, publish
  state.py      SQLite — the only thing that outlives a run
  gh/           the GitHub clients, REST and GraphQL
  tui/          the dashboard, and the only place Textual is imported
```

Inside `tui/`, outermost last:

| Layer | Modules | Depends on |
| --- | --- | --- |
| vocabulary | `theme`, `formatting`, `prose` | nothing |
| values and rules | `models`, `session`, `status`, `filling` | vocabulary |
| the store | `data` | values |
| rendering | `widgets`, `views/` | everything above |
| wiring | `app` | everything |

Everything below `views/` is pure: the clock is passed in rather than read, a key
press returns a new `Session` rather than mutating one, and a rendered pane is a
value you can compare.

Tests are standard library `unittest`, no runner to install:

```sh
.venv/bin/python -m unittest discover -s tests -t .
```

They cover the pure layers directly and drive the real dashboard headlessly
through Textual's pilot, which is what catches a layout or stylesheet mistake.
