#!/usr/bin/env bash
# Launcher.
#
# `./run.sh` starts the dashboard, setting up its one dependency the first time.
# `./run.sh --lean` is the plain scrolling log with no dependencies at all.
#
# Arguments that name a specific job — `--once`, `--check`, `--backfill` — run
# that job and exit, so they get the plain output and never install anything.
# A pipe or a cron job gets the same treatment: there is no terminal to draw a
# dashboard on, and nothing should sit waiting on a prompt.
#
# The dependency goes in a project-local .venv rather than your user site
# packages, so undoing it is `rm -rf .venv` and nothing outside this folder is
# touched.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
REQ="requirements-tui.txt"

dim()   { printf '\033[2m%s\033[0m\n' "$*"; }
warn()  { printf '\033[33m%s\033[0m\n' "$*" >&2; }
err()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }

# ---------------------------------------------------------------- interpreter

# Prefer the project venv when it exists — that is where Textual lives — and
# fall back to the system Python, which is all the reviewer itself needs.
pick_python() {
  if [ -x "$VENV/bin/python3" ]; then
    echo "$VENV/bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  else
    err "python3 is not on PATH."
    exit 1
  fi
}

PY="$(pick_python)"

"$PY" - <<'CHECK' || exit 1
import sys
if sys.version_info < (3, 10):
    sys.stderr.write(
        f"Python 3.10 or newer is required (found {sys.version.split()[0]}).\n"
    )
    raise SystemExit(1)
CHECK

has_textual() { "$1" -c "import textual" >/dev/null 2>&1; }

# ------------------------------------------------------------------- install

ensure_textual() {
  # Already available, either system-wide or in an existing venv.
  if has_textual "$PY"; then
    return 0
  fi

  if [ ! -x "$VENV/bin/python3" ]; then
    dim "Creating $VENV …"
    if ! python3 -m venv "$VENV" >/dev/null 2>&1; then
      warn "Could not create a virtual environment (python3-venv missing?)."
      return 1
    fi
  fi

  PY="$VENV/bin/python3"
  if has_textual "$PY"; then
    return 0
  fi

  dim "Installing the dashboard's dependency into $VENV (one time) …"
  if ! "$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1; then
    dim "  (could not upgrade pip; continuing)"
  fi
  if ! "$PY" -m pip install --quiet -r "$REQ"; then
    warn "Install failed. Falling back to plain mode."
    PY="$(pick_python)"
    return 1
  fi

  has_textual "$PY"
}

# ------------------------------------------------------------------ launching

# Arguments that mean "no dashboard": either asked for plainly, or a one-off
# job that prints its answer and exits. Everything else — no arguments at all,
# or modifiers like --debug and --dry-run — is the watch loop, which is what
# the dashboard is for.
wants_plain() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --lean|--check|--once|--init|--help|-h|--pr|--backfill|--backfill=*)
        return 0
        ;;
    esac
  done
  return 1
}

# No terminal to draw on: a pipe, a cron job, a CI step.
if [ ! -t 0 ] || [ ! -t 1 ]; then
  exec "$PY" -m reviewer "$@"
fi

if wants_plain "$@"; then
  exec "$PY" -m reviewer "$@"
fi

# Asked for by name — make sure the dependency is there, then hand over
# unchanged rather than passing --tui twice.
if [[ " $* " == *" --tui "* ]]; then
  ensure_textual || true
  exec "$PY" -m reviewer "$@"
fi

if ensure_textual; then
  exec "$PY" -m reviewer --tui "$@"
fi

warn "Starting the plain log instead. ./run.sh --lean skips this next time."
exec "$PY" -m reviewer "$@"
