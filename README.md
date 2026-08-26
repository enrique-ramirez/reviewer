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
