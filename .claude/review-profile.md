# Review profile

What the review agents need to know about this repository. They read this first and treat it as fact.

## Boundary

`reviewer/tui/` is the dashboard and depends on `reviewer/`. The core never depends on the dashboard, with one deliberate exception: `reviewer/__main__.py` imports `tui` lazily, inside the function that launches it, so a plain run needs neither Textual nor a terminal. Nothing else may import upward.

The second boundary is a security rule rather than an import rule, and it matters more. **The model gets a detached read-only checkout, an empty working directory, and no GitHub token.** `reviewer/model.py` and `reviewer/providers.py` are the only places that can break it. Flag anything that widens what a provider may read or write, that starts a CLI inside the tree being reviewed, or that lets the token reach a child environment.

## Excluded paths

- `.venv/`
- `config/repos/`, `config/global.json`, `.env` (private, gitignored)
- `logs/`, `debug/`, `*.sqlite3*`
- `*.local.md`, `*.local.txt`

## Functional directives

`# noqa` and `# type: ignore`. Never delete or weaken one.

## Docstrings

A docstring is a comment and is judged as one: it survives where a reader could not recover the fact from the code, and goes where it restates a name or a signature. Nothing here lints for their presence, so there is no floor to protect and no convention to keep a hollow one alive.

The ones carrying a real constraint are expected to survive. A module docstring explaining why `close()` runs first in the destructor, or why the sandbox uses an empty working directory, is exactly the case the test is meant to keep.

## Documentation map

| file | its job |
|---|---|
| `README.md` | what Blinky is, what it needs, and how to set it up. A pitch and a quickstart, not a reference |
| `FEATURES.md` | the reference: the dashboard, the three tabs, gates, providers, config, every flag |
| `CREDITS.md` | prior work this borrows from, and what was borrowed |
| `personality/` | the review voice, which is product rather than documentation. It is the system prompt |

There is no `CLAUDE.md` or `ARCHITECTURE.md` here. Anything architectural lives in a module docstring beside the code it describes, which is the convention worth keeping.

`personality/*.md` is not documentation about the tool. It is the text sent to the model, so a change there changes what reviews say. Treat it as product.

## Where an external fact belongs

A module docstring in the file that depends on it. GitHub API behaviour goes in `reviewer/gh/`, provider CLI behaviour in `reviewer/providers.py`, and anything a user needs to act on goes in `FEATURES.md` as well.

## Commands

| | |
|---|---|
| one test file | `.venv/bin/python -m unittest tests.test_waiting -q` |
| one case | `.venv/bin/python -m unittest tests.test_waiting.TestName.test_case` |
| everything | `.venv/bin/python -m unittest discover -s tests -q` |

The full run is 403 tests and about seventy-five seconds. It is affordable, but a review still runs only what covers the change.

## What cannot be verified here

Anything that reaches GitHub or starts a coding-agent CLI. The suite fakes both. A change to `reviewer/gh/` or `reviewer/providers.py` is reviewed against the fakes and is not proof the real thing still works.

## Voice

`enrique`, both for prose already in this repository and for anything the agents write into it, including their reports. Where that persona file is not in the checkout, which is the normal case on somebody else's machine, `house`.

The reviews this tool posts are governed separately, by `personality/`, which loads the same persona.

## Product decisions

The owner decides. A change to `personality/`, to a gate default, to what a review posts, or to what the model is allowed to read is a product decision, not an implementation detail. Finding one in a diff with no sign the owner was asked is a finding.

The outcome is not written down. The owner is the gatekeeper, not the documentation.
