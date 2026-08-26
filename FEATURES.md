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
- [Which model reviews](#which-model-reviews)
- [Token scoping](#token-scoping)
- [Configuration](#configuration)
- [Command line](#command-line)
- [Roadmap](#roadmap)
- [Working on it](#working-on-it)

## The dashboard

Three tabs, and the underlined letter in each name is its key — `d`, `s`, `h` —
working from anywhere.

Everything fits one screen at any terminal size: the tables shrink to the space
available rather than the window growing past the bottom.

What is true of the *run* sits in the header, next to the clock — the countdown
to the next scan, or the phase and elapsed time while one is going. What is true
of the *list* you are looking at sits under it, inside its column, with the
divider running past to say so: how many rows, which page, and the filters at the
right-hand end of the same line. Nothing describing a list runs under the detail
pane beside it. `l` hides the log pane
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
elapsed time beside the phase in the header. A review is minutes of work,
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
and how many comments we left before it landed.

The detail pane is three named sections — **what it changed**, **the change**,
**our part in it** — with the summary given a bar down its edge, because on a
merged pull request that sentence is the thing you came to read and everything
else is context for it. Underneath sit two buttons: whatever there is to do with
this record on the left, the way out to GitHub on the right. Each one underlines
its shortcut, and does exactly what that key does.

This tab is close to free. Only pull requests we reviewed are looked up, so a
merge nobody asked us about costs nothing; the summary is one small call with no
tools and no checkout, written once and kept; and where the reviewer already
described the change during its review, that description is reused and no diff is
fetched at all.

### History — everything on record

The same record without the time limit, across every repository you watch.
Filter by author with `/` — which answers "what has this person shipped since
March" — pick a date range from the **dates** control with `t`, and clear the lot
with `Escape`.

The date control shows the range as well as setting it, so there is no separate
label to read to find out where you landed, and no cycling through four options
to reach the one you wanted. `t` opens the list; the arrows and `Enter` choose;
`Escape` backs out. Picking one hands the keyboard back to the table.

It is paged rather than scrolled: a page is however many rows your window can
show, and only that many are read from the database, so a history of two thousand
merges costs the same to open as one of twenty. **Moving down past the last row
turns the page** — `j` and the arrows carry on rather than stopping — and `[` /
`]` or `PgUp` / `PgDn` jump a page at a time. The status line says which page of
how many you are on, which is the thing an endless scroll cannot tell you.

It lives in the same SQLite database as the rest of the state, under
`~/.local/state/blinky/`, and outlives the pull requests it describes.

### Keys

The footer follows the tab you are on, so it only ever offers keys that do
something where you are — and it is the only place the keys are listed. There
used to be a second, dimmer list above the status line; two copies of the same
thing meant the one you could read was the one that was out of date.

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
| `t` | History | open the date-range picker |
| `[` `]` / `PgUp` `PgDn` | History | turn the page |
| `j` / `k` at a page edge | History | carries on to the next page |
| `b` | History | fill in past history (press again to stop) |
| `g` | History | write a summary for the selected merge — one model call |
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

### Summarising one of them anyway

Free history is the right default — a sweep of two thousand merges should not
cost two thousand model calls. But the odd row is worth reading properly, and
that is what `g` is for: put the cursor on any merge in the History tab and press
it.

What comes back is stored exactly as a summary written during a tick would be —
same column, same source, kept for good. Read it once and it is there next time;
nothing re-asks for it, and nothing overwrites it.

- It reads the pull request and its file list from GitHub, then makes **one**
  model call, on whatever `merge_summary` is pointed at — the cheapest tier you
  have configured.
- Rows that already carry a written summary are refused rather than rewritten, so
  a stray keypress cannot buy the same sentence twice.
- Press it on several rows and they queue, one call at a time.
- It runs on its own thread, so the reviewer carries on behind it, and the
  History status bar says which one is being written.

The Summary tab exists because automating review makes it easy to stop noticing
what is landing. This is the same idea pointed backwards: the changes that landed
before the tool was watching.

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
2. **The repository's own docs** — `AGENTS.md`, `CLAUDE.md`, and whatever else
   `repo_context.paths` matches, read from the default branch. Whichever agent
   the team wrote them for; this has nothing to do with which provider does the
   reviewing. This is where a
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
structured JSON out. It can read a clean checkout of the PR head, so it can look
at surrounding code to judge whether a change follows the repo's conventions. It
cannot write, cannot run a build, cannot reach the network, and never sees a
GitHub token — that is stripped from the child environment along with anything
else GitHub-shaped.

Its working directory is an empty scratch dir, never the checkout. All of these
CLIs auto-load instructions from wherever they start — `CLAUDE.md`, `AGENTS.md`,
`GEMINI.md` — so starting one inside the tree it is reviewing would let a pull
request write instructions to its own reviewer. The checkout is named in the
prompt and reached by absolute path instead.

*How* reading is confined depends on which CLI you point it at, and the
difference is worth knowing before you switch — see
[Which model reviews](#which-model-reviews).

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

## Which model reviews

Every provider here is a **terminal coding agent** you have already installed and
signed in. There is no HTTP client anywhere in this tool and no API key in your
config: a review spends the quota that CLI already has.

| `type` | Runs | How reading is fenced off |
|---|---|---|
| [`claude`](#claude-code) | `claude -p` | Tool allowlist **and** denylist |
| [`codex`](#codex) | `codex exec --sandbox read-only` | Filesystem sandbox — [wider](#codex) |
| [`gemini`](#gemini-cli) | `gemini --output-format json` | Tool allowlist, writes refused at approval |
| [`command`](#command) | anything you name | Nothing. You vouch for it |

Two lines pick one:

```json
"provider": "codex",
"providers": { "codex": { "model": "gpt-5.1-codex" } }
```

An entry named after a known type does not have to repeat it. Any other name is
a profile and must say what it is — which is how you keep two settings of the
same CLI around:

```json
"providers": {
  "cheap": { "type": "claude", "model": "claude-haiku-4-5-20251001" }
}
```

Bad names are caught at startup, not fifteen minutes into a watch loop on the one
pull request that needed the call. `./run.sh --check` goes further and confirms
every CLI a tick could reach for is actually on `PATH` and runs.

### Per repository

A repo config's `model` block names a provider, overrides its settings, or both.
Anything it sets is layered over the named entry, so pinning just the model
leaves the command, tools and timeout alone:

```json
"model": { "model": "claude-sonnet-5" }
```

```json
"model": { "provider": "codex", "model": "gpt-5.1-codex" }
```

A `null` means *inherit*, not *unset*, which is what lets the sample config ship
the block with every key blank.

### Per kind of call

Not every call is a review. Two of them are much smaller jobs, and each has its
own block in `global.json` that layers over whatever provider the repo resolved
to — plan on the expensive model, follow up on a cheap one:

| Block | What it is | Sends a diff? | Tools? |
|---|---|---|---|
| *(the review)* | The full pass over a PR | Yes, the whole bundle | Yes |
| `thread_reply` | Answering one thread that has a new reply | **No** — just the conversation | Yes |
| `merge_summary` | The one-line "what changed" on the Summary tab | No | **No** |

```json
"thread_reply":  { "model": "claude-sonnet-5" },
"merge_summary": { "model": "claude-haiku-4-5-20251001" }
```

`thread_reply` keeps its tools because checking whether a claim holds up against
the code is most of the point of answering. `merge_summary` drops them because it
has nothing to look at. Setting `provider` on either sends that kind of call to a
different CLI entirely, and drops any repo-level model pin along with it — a
model name pinned for one provider means nothing to another.

### What every provider does the same way

Four things are true whichever one you pick, because the guarantees in this file
rest on them rather than on any one vendor's flags:

- **The prompt goes in on stdin**, never as an argument. A review bundle would
  hit `ARG_MAX` long before it hit anything else.
- **The working directory is an empty scratch dir**, never the checkout — see
  [above](#what-the-model-can-and-cannot-do) for why that one matters most.
- **`GITHUB_TOKEN` and friends are stripped** from the child environment.
- **The call is killed on quit**, along with any children it spawned.

`allowed_tools` is written in one vocabulary — `Read`, `Glob`, `Grep` — and each
adapter translates it into whatever its CLI calls those things. Those happen to
be Claude Code's names, because that is what this config already spoke; they are
the canonical spelling here rather than a statement about which provider is
in charge. Names an adapter does not recognise are passed through untouched, so
a provider-specific list works too.

CLI flags drift between releases faster than any adapter can. Every provider's
`extra_args` is appended verbatim to each invocation — that is the escape hatch,
and reaching for it is expected rather than a sign something is wrong.

### What changes when you switch

The rest is not vendor-neutral, and pretending otherwise would be the wrong kind
of documentation. Each provider gets its own list below.

#### Claude Code

`"type": "claude"` · `claude -p --output-format json`

The default, and the reason the other defaults look as they do: it is the only
one here that takes both an explicit tool allowlist and an explicit denylist, so
*may read files, may not run anything* is a fact about the process rather than a
hope about the model.

- `allowed_tools` is passed through unchanged — these are its own tool names.
- The denylist (`Bash`, `Write`, `Edit`, `WebFetch`, `Task`, …) is sent on every
  call, including the tool-less merge summary.
- The system prompt travels in `--append-system-prompt`, separate from the diff.
- No known gotchas. If you have no reason to move, do not.

#### Codex

`"type": "codex"` · `codex exec --sandbox read-only --skip-git-repo-check`

- **Its read-only sandbox is read-only about *writing*.** Within it the model may
  run read-only shell commands, and may read paths outside the checkout —
  including the rest of your home directory. That is a wider read surface than
  the two allowlist-based providers, where the tool list is the boundary. It
  still cannot write, install, or reach the network, and the token is stripped
  either way — but if the scoping above is why you run this tool, that is the
  line that moves.
- **`allowed_tools` is not read.** There is no tool list to apply it to, so
  setting it there does nothing — including the empty list the merge summary
  uses, which means summaries run with the same sandbox as reviews rather than
  with nothing.
- **No `--append-system-prompt`.** Your personality is folded into the top of the
  user message inside `<reviewer_instructions>` instead. Same text, different
  envelope.
- `--skip-git-repo-check` is required because the scratch working directory is
  deliberately not a git repository.
- The answer is read from `--output-last-message`, with the JSONL event stream as
  a fallback, so one empty output channel does not cost a review.

#### Gemini CLI

`"type": "gemini"` · `gemini --output-format json --approval-mode default`

- **Writes are blocked by withholding approval, not by dropping the tools.** A
  write needs a confirmation a non-interactive run cannot give, so it fails.
  **Do not put `--approval-mode yolo` in `extra_args`** — one flag is all that
  stands between this and a model with edit rights on the checkout.
- `--include-directories` grants write access as well as read, which is why the
  point above is load-bearing rather than belt-and-braces.
- `allowed_tools` is translated: `Read` → `read_file`, `Glob` → `glob`,
  `Grep` → `search_file_content`.
- **No `--append-system-prompt`.** Folded into the message, as with Codex.

#### command

`"type": "command"` · whatever you named, with `extra_args` as the command line

The escape hatch: any CLI that takes a prompt on stdin and prints an answer on
stdout. Requires an explicit `command`; there is no default to guess.

- **Nothing here can restrict what that command does.** It gets whatever it gives
  itself — no allowlist, no sandbox, no denylist. Point it at something
  read-only, and treat it as a program you are vouching for.
- The system prompt is folded into the message; the reply is read straight from
  stdout with no envelope to unwrap.

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
whether every configured model CLI is reachable. Run it whenever the token
changes.

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

State and logs go to `~/.local/state/blinky/`, deliberately outside the
checkout — they hold PR titles and, under `--debug`, diff content. Logs record
repo, PR number, decision and token counts; diff and comment bodies are written
only when you pass `--debug`.

## Command line

```
./run.sh [options]

  --init              Write the config files by asking a few questions, then
                      exit. Never overwrites; safe to re-run.
  --check             Probe what the token can reach, check the account and the
                      model CLIs, name any missing permission, and exit.
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
  --state-dir PATH    Override ~/.local/state/blinky
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

### What a review cost

Every pass records what it took, and the detail panes show it. On the Dashboard,
under `last pass` — the most recent round:

```
last pass
  COMMENT
  1m 59s · 2 calls · 1.1k out · 42.3k in · 41.8k cached · $0.448
  claude · claude-opus-5
```

On History, totalled over every round a pull request took before it merged:

```
from us    4 comment(s) over 2 round(s)
ended on   APPROVE
cost       4m 46s · 86.3k tokens · $0.448   claude · claude-opus-5
```

`2 calls` appears only when the axes were split. Fresh and cached input are kept
apart because only the fresh half responds to trimming a prompt, and the two are
billed nothing like the same.

Every part is conditional, because providers report different things — Claude
Code gives a price, others give tokens and no price, some give neither. A
provider that reported nothing drops the line rather than printing a zero, and so
do reviews from before this was recorded: *nobody counted* and *it cost nothing*
look identical on screen and mean opposite things.

### What a dry run tells you about cost

Every model call in a dry run logs what its prompt was made of, largest section
first — the place to find out whether `max_total_lines` or `personality/` is what
is actually costing you:

```
                                          chars  est. tok
  system
    repository documentation              9,841     2,660
    following the local shape             4,013     1,085
    voice — the comments people read      3,956     1,069
  user
    diff                                 36,009     9,732
    pr description                          899       243
  ───────────────────────────────────────────────────────
  sent by this tool                      67,396    18,215
  counted in by the provider                       41,921
  ↳ of which CLI overhead                          23,706   fixed; not editable here
  ↳ of which read from cache                       20,902   billed at a fraction
  out                                               1,130
```

Read it in three parts. **Sections** are estimated from character counts — no
tokeniser ships with the standard library — so treat them as ±15% and as a way to
compare rows against each other, which is what tuning needs. **CLI overhead** is
the coding-agent CLI's own system prompt and tool definitions; it is real, it is
usually the second-largest line, and nothing in this repository can change it.
**Read from cache** is the one to protect: the system prompt is identical across
every PR in a repo, so it caches and is billed at a fraction. Anything that makes
the system prompt vary per pull request would quietly cost you that.

With `--debug` the same table is written to `tokens-<axis>.txt` in the state dir.

## Roadmap

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
  model.py      the subprocess: one prompt in, one JSON object out
  providers.py  one adapter per coding-agent CLI
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
