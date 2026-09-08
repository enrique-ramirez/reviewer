#!/usr/bin/env bash
# Flags the style regressions that are mechanically decidable. Comment *quality* is not,
# and is not attempted here: the review agents own that. This only catches what a grep can
# be sure about, so a hit is a real violation rather than a suggestion.
#
# Handles two PostToolUse events. Given `tool_input.file_path` it checks that one file.
# Given `tool_input.command` it works out which files the command wrote and checks those,
# which is what stops a heredoc or a `sed -i` from walking past every rule here.
set -uo pipefail

: "${CLAUDE_PROJECT_DIR:=$PWD}"

payload=$(cat)
read_field() {
  printf '%s' "$payload" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('$1',''))" 2>/dev/null
}

# Where the shared voice files live. A plugin install has CLAUDE_PLUGIN_ROOT; a copy
# install puts them under the project's own .claude/.
# Three layouts: a plugin install, a copy install, and the kit checked out as itself.
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/voice/register.txt" ]; then
  kit_root="$CLAUDE_PLUGIN_ROOT"
elif [ -f "$CLAUDE_PROJECT_DIR/.claude/voice/register.txt" ]; then
  kit_root="$CLAUDE_PROJECT_DIR/.claude"
else
  kit_root="$CLAUDE_PROJECT_DIR"
fi
register="$kit_root/voice/register.txt"
checker="$kit_root/hooks/check-prose.py"
local_register="$CLAUDE_PROJECT_DIR/.claude/register.local.txt"
local_patterns="$CLAUDE_PROJECT_DIR/.claude/style-patterns.local.txt"
exempt_list="$CLAUDE_PROJECT_DIR/.claude/style-exempt.txt"

[ -f "$checker" ] || exit 0

exempt() {
  # Files whose subject is the rules must quote the phrases the rules forbid, so they
  # cannot be checked by them.
  case "$1" in
    */third_party/*|*/vendor/*|*/sdk/*|*/node_modules/*|*/.git/*|*/dist/*|*/build/*) return 0 ;;
    *.local.md|*.local.txt|*/_todo/*|*/_temp/*) return 0 ;;
    */.claude/*|*/CONTRIBUTING.md) return 0 ;;
    *.lock|*.snap|*.min.js|*.min.css) return 0 ;;
  esac
  if [ -f "$exempt_list" ]; then
    while IFS= read -r glob; do
      case "$glob" in ""|\#*) continue ;; esac
      # shellcheck disable=SC2254  # the pattern is the point; it must stay unquoted
      case "$1" in $glob) return 0 ;; esac
    done < "$exempt_list"
  fi
  return 1
}

problems=()

check_file() {
  local file="$1" label="${1#"$CLAUDE_PROJECT_DIR/"}"

  # Identity and infrastructure detail. Public repositories; placeholders instead. The
  # patterns are yours and live outside version control, so this file never publishes
  # the hostnames it exists to keep out.
  if [ -f "$local_patterns" ]; then
    local deny allow hits
    deny=$(grep -vE '^\s*(#|-|$)' "$local_patterns" 2>/dev/null | paste -sd '|' -)
    # A `-` line allows a match on the same line, so a pattern can name a person and
    # still let their repository URL through.
    allow=$(grep -E '^-' "$local_patterns" 2>/dev/null | cut -c2- | paste -sd '|' -)
    if [ -n "$deny" ]; then
      hits=$(grep -nIE "$deny" "$file" 2>/dev/null)
      [ -n "$allow" ] && hits=$(printf '%s' "$hits" | grep -vE "$allow")
      if [ -n "$hits" ]; then
        problems+=("$label: identity or infrastructure detail. Use a placeholder.")
      fi
    fi
  fi

  # History. Git holds it.
  if grep -nIE 'measured on [0-9]|(during|after|in) a review pass|an earlier version|this used to|it used to|used to read|used to be|the first version|learned the hard way|we moved to a' "$file" >/dev/null 2>&1; then
    problems+=("$label: records history (what something used to be, or a dated measurement). Git holds that; state only what is true now.")
  fi

  # Markdown paragraphs hard-wrapped near 80 columns. A repository that wraps on purpose
  # switches this off with a `-hardwrap` line in its local register file.
  case "$file" in
    *.md)
      grep -qxF -- '-hardwrap' "$local_register" 2>/dev/null || \
      if python3 - "$file" <<'PY' >/dev/null 2>&1
import re,sys
lines=open(sys.argv[1],encoding='utf-8',errors='replace').read().split('\n')
fenced=False; run=0
for ln in lines:
    if ln.lstrip().startswith('```'): fenced=not fenced; run=0; continue
    prose=(not fenced and ln.strip() and not re.match(r'^\s*([|>#\-*+]|\d+\.|\s{4})',ln))
    run = run+1 if prose and 60<=len(ln)<=92 else 0
    if run>=3: sys.exit(0)
sys.exit(1)
PY
      then problems+=("$label: looks hard-wrapped near 80 columns. One paragraph is one line; editors wrap.")
      fi ;;
  esac

  # Register and constructions. Prose only, so an identifier or a fixture is never checked.
  local prose
  prose=$(python3 "$checker" "$file" "$register" "$local_register" 2>/dev/null)
  if [ -n "$prose" ]; then
    while IFS= read -r entry; do problems+=("$label: $entry"); done <<< "$prose"
  fi
}

file=$(read_field file_path)

if [ -n "$file" ]; then
  { [ -f "$file" ] && ! exempt "$file"; } || exit 0
  check_file "$file"
else
  command=$(read_field command)
  [ -n "$command" ] || exit 0
  # Only commands that could have written a file are worth the scan below.
  printf '%s' "$command" | grep -qE '(>|>>|<<|\btee\b|\bsed\b[^|]*-i|\bcp\b|\bmv\b|\binstall\b|\bpatch\b|\bdd\b|\btruncate\b|git +apply|\bpython3?\b|\bnode\b|\bperl\b)' || exit 0

  git_dir=$(git -C "$CLAUDE_PROJECT_DIR" rev-parse --git-dir 2>/dev/null) || exit 0
  case "$git_dir" in /*) ;; *) git_dir="$CLAUDE_PROJECT_DIR/$git_dir" ;; esac
  snapshot="$git_dir/style-check-snapshot"

  # Checksums rather than timestamps. A command that writes a file within the same
  # second as the last scan is the normal case, not the rare one, and mtime on this
  # platform cannot tell those apart.
  current=$(
    git -C "$CLAUDE_PROJECT_DIR" status --porcelain --untracked-files=all 2>/dev/null |
      cut -c4- | head -200 |
      while IFS= read -r path; do
        [ -f "$CLAUDE_PROJECT_DIR/$path" ] || continue
        printf '%s %s\n' "$(cksum < "$CLAUDE_PROJECT_DIR/$path" | cut -d' ' -f1)" "$path"
      done
  )

  # First run in a checkout only records the snapshot. Reporting every dirty file at
  # that point would dump a backlog nobody asked for.
  if [ ! -f "$snapshot" ]; then
    printf '%s\n' "$current" > "$snapshot"
    exit 0
  fi

  checked=0
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    grep -qxF "$entry" "$snapshot" && continue
    path=${entry#* }
    full="$CLAUDE_PROJECT_DIR/$path"
    [ -f "$full" ] || continue
    exempt "$full" && continue
    check_file "$full"
    checked=$((checked + 1))
    [ "$checked" -ge 10 ] && break
  done <<< "$current"
  printf '%s\n' "$current" > "$snapshot"
fi

[ ${#problems[@]} -eq 0 ] && exit 0

{
  echo "Style check:"
  for p in "${problems[@]}"; do echo "  - $p"; done
  echo "See voice/constructions.md and the contributing guide. Fix it now rather than leaving it for a later pass."
} >&2
exit 2
