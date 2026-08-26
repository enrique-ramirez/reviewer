# ᗣ Blinky

**Your code review, running without you.** One terminal window, on your own
machine, watching your repositories and posting reviews to GitHub under your
account — in your voice, clearly labelled as AI-written.

Named after Pac-Man's red ghost, who is already waiting at the end of the
countdown.

Between scans, a Pac-Man eats his way along a line of dots toward a ghost, so
you know how long you have. Fold the sidebar away and your repositories become
ghosts too, one each, still glowing yellow if something there wants you. This is
a tool you leave open all day. It may as well be nice to look at.

```
  ᗧ······················ᗣ   next scan 11m04s
```

## What it is

A Python TUI wrapped around a coding-agent CLI. It watches pull requests, decides
which ones are worth a model call, sends a compressed diff, and posts the result
as a real GitHub review — inline comments, a summary, and a collapsible **Prompt
for AI agents** block your teammates can paste straight into their own agent.

You need Python 3.10+, `git`, a GitHub fine-grained token, and one coding-agent
CLI signed in — `claude`, `codex` or `gemini`, whichever you already have, or
anything else that takes a prompt on stdin. That is the whole list: the reviewer
itself is standard library only, and `./run.sh` puts the dashboard's one
dependency in a project-local `.venv` the first time you run it. Reviews go
through that CLI's subscription rather than a metered API key, so a review costs
quota, not dollars.

## Why it exists

Review is the first thing a busy team drops and the most expensive thing to have
dropped. The usual fix is a bot that leaves forty comments about naming, which
teaches everyone to skim past the bot.

So this one is built the other way round:

- **It reviews like you, not like a linter.** Your voice, your severity bar,
  your idea of what is worth saying, all in `personality/` as markdown you own
  and can take to the next job. Three real findings that get acted on beat
  twenty-five that get closed.
- **It is cheap on purpose.** Same head SHA and no new comments? No work, no
  call. On a quiet repo it costs essentially nothing to leave running.
- **It keeps you in the loop.** The **Summary** tab tells you what merged while
  you were watching and what each change actually did — because automating the
  reviewing should not mean losing track of what is going into your repo.

## Quick start

```sh
git clone <this repo> && cd blinky
./run.sh --init     # a few questions, writes the config
./run.sh --check    # can the token reach everything?
./run.sh            # go
```

Then open `personality/` and make it sound like you. That is the part worth your
time; everything else already has a sensible default.

## The token

`./run.sh --init` asks for one and writes `.env`; if you would rather do it by
hand, copy [`config/.env.sample`](config/.env.sample) to `.env` at the repo root
and fill in `GITHUB_TOKEN`. Either way the token itself comes from
<https://github.com/settings/personal-access-tokens> → **Generate new token**.

Make it a **fine-grained** token, not a classic one:

- **Resource owner** — your own account for personal repositories, or the
  organisation that owns them. Choosing an organisation means an owner has to
  approve the token before it works, and it stays inert until they do.
- **Repository access** — *Only select repositories*, and pick exactly the ones
  you list in `config/repos/`.
- **Repository permissions** —

  | | |
  |---|---|
  | Contents | **Read-only** |
  | Pull requests | **Read and write** |
  | Commit statuses | **Read-only** |
  | Actions | **Read-only** |
  | Metadata | Read-only (mandatory, selected for you) |

`Pull requests: Read and write` is the minimum that allows posting reviews,
replying to threads and resolving conversations. It is also all that is needed to
read whether a pull request has been approved — that comes back on the pull
request itself, and no separate permission covers it.

`Contents: Read-only` is the load-bearing part: with it, this tool cannot push a
commit, create a branch, or merge anything, whatever else goes wrong.

`Commit statuses` and `Actions` are how it sees whether CI is green — see below
for why it takes two of them. Finish by running `./run.sh --check`, which probes
every permission one at a time and names the exact one behind any failure.

### Why there is no `Checks` permission to tick

The permission that actually covers CI results is `Checks`, and GitHub does not
offer it on fine-grained tokens — it exists only for GitHub Apps. That is a
deliberate restriction, not something waiting on an organisation owner to
approve, and it is
[not reflected in their docs](https://github.com/orgs/community/discussions/179545),
which is why every guide tells you to tick a box that is not there.

Without it GitHub refuses the `statusCheckRollup` field, so the reviewer falls
back to reading CI a coarser way, and neither fallback is complete:

- **Actions: Read-only** covers everything running in GitHub Actions, but not a
  check posted by a third-party App.
- **Commit statuses: Read-only** covers integrations that post statuses, and
  *not* Actions check runs.

`./run.sh --check` reports which fallbacks your token can reach, how many
workflows or statuses each one actually sees, and the `"gates": { "ci_source":
... }` line to put in the repo config. Compare its counts against what the pull
request page shows before trusting one. If neither reaches anything, the honest
options are `"gates": { "require_ci_green": false }` — the reviewer will then
review pull requests whose builds are red — or a classic PAT with the `repo`
scope, which can read check runs but also grants write access to code and so
gives up the guarantee that this tool cannot push.

`Code quality` is unrelated, despite sounding like the one you want: it reads
findings from GitHub's paid Code Quality product and says nothing about CI or
about approvals. Leave it off.

## What it will not do

The token is `Contents: Read-only`, so it cannot push a commit, create a branch,
or merge anything, whatever else goes wrong. The model gets read-only access to a
clean, detached checkout and nothing else — no writes, no network, no GitHub
token, no sight of your working copy. Exactly how that read access is fenced off
depends on which CLI you point it at, and
[FEATURES.md](FEATURES.md#which-model-reviews) says plainly what moves when you
switch. Everything that touches GitHub is done by the script, from JSON the
script validated first, including checking that every comment line actually
exists in the diff.

It can approve, because a review that can never unblock a merge is not doing the
job. If you would rather watch it for a week first, set `approval.mode` to
`"manual"` and it will draft and notify without posting.

## More

**[FEATURES.md](FEATURES.md)** — the dashboard, the three tabs, watching several
repositories, filling in history, what gets sent to the model, choosing a
provider and pinning a different one per repository, token scoping, the full
config reference, and every flag.

## License

MIT — see [LICENSE](LICENSE).

## Credits

See [CREDITS.md](CREDITS.md).
